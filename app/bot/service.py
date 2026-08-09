"""Синхронные операции с БД/LLM для бота (вызываются через asyncio.to_thread)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from pathlib import Path

from app.config import settings
from app.db import session_scope
from app.images.generator import add_style_reference, generate_notification_image
from app.llm.generation import (
    create_draft_posts,
    generate_drafts,
    generate_meme_captions,
    sanitize_notification,
)
from app.models import (
    Account,
    AccountMetric,
    Archetype,
    Post,
    PostSource,
    PostStatus,
    TrendTopic,
)
from app.tasks.reports import ARCHETYPE_LABELS, _latest_metric


def get_account_id() -> int | None:
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        return account.id if account else None


def list_unused_trends(limit: int = 6) -> list[tuple[int, str]]:
    with session_scope() as session:
        topics = session.scalars(
            select(TrendTopic)
            .where(TrendTopic.used.is_(False))
            .order_by(TrendTopic.relevance_score.desc(), TrendTopic.fetched_at.desc())
            .limit(limit)
        ).all()
        return [(t.id, t.headline) for t in topics]


def refresh_trends() -> list[tuple[int, str]]:
    """Ручной поиск тем: сбрасывает старый неиспользованный пул и заново
    прогоняет текущую ленту через актуальный фильтр (иначе на неизменной ленте
    вернулись бы те же темы, включая сохранённые старым, слабым фильтром)."""
    from sqlalchemy import delete

    from app.tasks.trends import fetch_trend_topics

    with session_scope() as session:
        session.execute(delete(TrendTopic).where(TrendTopic.used.is_(False)))
    fetch_trend_topics()  # celery-задача, выполняется синхронно в этом потоке
    return list_unused_trends()


def generate_drafts_and_save(
    trend_topic_id: int | None = None,
    custom_headline: str | None = None,
    n_drafts: int = 4,
) -> list[dict]:
    """Генерирует тексты черновиков и сохраняет в БД (БЕЗ картинок — они делаются
    отдельно и параллельно, чтобы бот не подвисал).

    Возвращает [{post_id, archetype, text, has_image}], где has_image=True для
    POV-постов со скриншотом-мокапом.
    """
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise RuntimeError("Аккаунт Threads не подключён.")

        trend_headline = custom_headline
        if trend_topic_id is not None:
            topic = session.get(TrendTopic, trend_topic_id)
            if topic:
                trend_headline = topic.headline
                topic.used = True

        drafts = generate_drafts(
            session, account.id, n_drafts=n_drafts, trend_headline=trend_headline
        )
        posts = create_draft_posts(session, account.id, drafts)

        results = []
        for post, draft in zip(posts, drafts):
            # скриншот генерится ТОЛЬКО для POV-формата (pov_confession);
            # мемы приходят загрузкой фото, остальное — текст
            notification = draft.get("notification")
            is_pov = post.archetype == Archetype.pov_confession
            first_comment = (draft.get("first_comment") or "").strip() or None
            meta: dict = {}
            if notification and is_pov:
                meta["notification"] = notification
            if first_comment:
                meta["first_comment"] = first_comment  # закрепляемый коммент
            if meta:
                post.meta = meta
            results.append(
                {
                    "post_id": post.id,
                    "archetype": post.archetype.value,
                    "text": post.text,
                    "has_image": bool(notification and is_pov),
                    "first_comment": first_comment,
                }
            )
        return results


def generate_training_batch(n: int = 5) -> list[dict]:
    """Генерирует посты для разметки голоса (без картинок — быстро).

    Возвращает [{post_id, archetype, text}].
    """
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise RuntimeError("Аккаунт Threads не подключён.")
        drafts = generate_drafts(session, account.id, n_drafts=n)
        posts = create_draft_posts(session, account.id, drafts)
        return [
            {"post_id": p.id, "archetype": p.archetype.value, "text": p.text}
            for p in posts
        ]


def set_voice_rating(post_id: int, rating: str) -> bool:
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None:
            return False
        post.voice_rating = rating
        return True


def approve_image_reference(post_id: int) -> bool:
    """Одобренный скриншот -> в пул референсов стиля (source='approved')."""
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None or not post.image_url:
            return False
        fname = post.image_url.rsplit("/", 1)[-1]
        path = Path(settings.media_dir) / fname
        if not path.exists():
            return False
        add_style_reference(session, path.read_bytes(), source="approved")
        post.voice_rating = "good"
        return True


def recent_published(limit: int = 10) -> list[dict]:
    with session_scope() as session:
        posts = session.scalars(
            select(Post)
            .where(Post.status == PostStatus.published)
            .order_by(Post.published_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "post_id": p.id,
                "text": p.text[:60].replace("\n", " "),
                "brought_lead": p.brought_lead,
            }
            for p in posts
        ]


def toggle_lead(post_id: int) -> bool | None:
    """Переключает пометку «пост привёл запрос». Возвращает новое значение."""
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None:
            return None
        post.brought_lead = not post.brought_lead
        return post.brought_lead


def rating_counts() -> tuple[int, int]:
    with session_scope() as session:
        good = session.scalar(
            select(func.count()).select_from(Post).where(Post.voice_rating == "good")
        )
        bad = session.scalar(
            select(func.count()).select_from(Post).where(Post.voice_rating == "bad")
        )
        return good or 0, bad or 0


def _save_uploaded_image(image_bytes: bytes) -> str:
    """Сохраняет загруженный мем в media, возвращает публичный URL."""
    import uuid

    media_dir = Path(settings.media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    fname = f"meme_{uuid.uuid4().hex}.jpg"
    (media_dir / fname).write_bytes(image_bytes)
    return f"{settings.public_base_url.rstrip('/')}/media/{fname}"


def create_meme_draft(image_bytes: bytes) -> dict:
    """Загруженный мем -> 3 варианта подписи. Создаёт draft-пост с картинкой и
    вариантами в meta. Возвращает {post_id, image_url, captions}."""
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise RuntimeError("Аккаунт Threads не подключён.")
        url = _save_uploaded_image(image_bytes)
        captions = generate_meme_captions(session, account.id, image_bytes, n=3)
        post = Post(
            account_id=account.id,
            source=PostSource.client_own,
            archetype=Archetype.meme_notification,
            text=captions[0] if captions else "",
            image_url=url,
            status=PostStatus.draft,
            meta={"caption_options": captions},
        )
        session.add(post)
        session.flush()
        return {"post_id": post.id, "image_url": url, "captions": captions}


def choose_meme_caption(post_id: int, idx: int) -> str | None:
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None or not post.meta:
            return None
        options = post.meta.get("caption_options") or []
        if not (0 <= idx < len(options)):
            return None
        post.text = options[idx]
        return options[idx]


def set_post_status(post_id: int, status: PostStatus) -> bool:
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None:
            return False
        post.status = status
        return True


def update_post_text(post_id: int, text: str) -> bool:
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None:
            return False
        post.text = text
        return True


def regenerate_post_image(post_id: int) -> str | None:
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None or not post.meta or "notification" not in post.meta:
            return None
        n = post.meta["notification"]
        url = generate_notification_image(
            session, n["app"], n["sender"], n["message"], n.get("amount")
        )
        post.image_url = url
        return url


def _post_image_bytes(post: Post) -> bytes | None:
    """Байты текущего скриншота поста (локальный файл) — чтобы сохранить того же
    ученика/фон при смене текста."""
    if not post.image_url:
        return None
    path = Path(settings.media_dir) / post.image_url.rsplit("/", 1)[-1]
    return path.read_bytes() if path.exists() else None


def change_image_text(post_id: int, instruction: str) -> dict | None:
    """Меняет текст на скриншоте по вольной инструкции учителя (через санитайзер),
    СОХРАНЯЯ того же ученика и фон (предыдущий скрин идёт референсом), и
    перегенерирует картинку. Возвращает {url, message, app}."""
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None or not post.meta or "notification" not in post.meta:
            return None
        new_notif = sanitize_notification(post.meta["notification"], instruction)
        post.meta = {"notification": new_notif}
        url = generate_notification_image(
            session,
            new_notif["app"],
            new_notif["sender"],
            new_notif["message"],
            new_notif.get("amount"),
            base_image_bytes=_post_image_bytes(post),
        )
        post.image_url = url
        return {"url": url, "message": new_notif["message"], "app": new_notif["app"]}


def list_sequelable_posts(limit: int = 8) -> list[tuple[int, str]]:
    """Последние POV-посты со скриншотом (есть meta.notification + image_url),
    любого статуса — кандидаты на продолжение серии. Возвращает [(id, label)]."""
    with session_scope() as session:
        posts = session.scalars(
            select(Post)
            .where(
                Post.archetype == Archetype.pov_confession,
                Post.image_url.isnot(None),
                Post.meta.isnot(None),
            )
            .order_by(Post.created_at.desc())
            .limit(40)
        ).all()
        out: list[tuple[int, str]] = []
        for p in posts:
            if not (p.meta and p.meta.get("notification")):
                continue
            n = p.meta["notification"]
            who = n.get("sender", "")
            preview = (p.text or n.get("message", "")).replace("\n", " ")[:38]
            out.append((p.id, f"{who}: {preview}"))
            if len(out) >= limit:
                break
        return out


def create_continuation_draft(post_id: int) -> dict | None:
    """Продолжение серии: новый черновик с ТЕМ ЖЕ учеником (тот же скрин идёт
    референсом) и следующим сообщением от него. Возвращает
    {post_id, archetype, text, url} или None, если у поста нет данных скриншота."""
    from app.llm.generation import generate_sequel

    with session_scope() as session:
        src = session.get(Post, post_id)
        if src is None or not src.meta or "notification" not in src.meta:
            return None
        account_id = src.account_id
        base_bytes = _post_image_bytes(src)
        seq = generate_sequel(session, account_id, src.text, src.meta["notification"])
        notif = seq["notification"]
        new_post = Post(
            account_id=account_id,
            source=PostSource.generated,
            archetype=Archetype.pov_confession,
            text=seq["caption"],
            status=PostStatus.draft,
            meta={"notification": notif},
        )
        session.add(new_post)
        session.flush()
        url = generate_notification_image(
            session,
            notif["app"],
            notif["sender"],
            notif["message"],
            notif.get("amount"),
            base_image_bytes=base_bytes,
        )
        new_post.image_url = url
        return {
            "post_id": new_post.id,
            "archetype": new_post.archetype.value,
            "text": new_post.text,
            "url": url,
        }


def add_own_post(text: str) -> int:
    """Собственный пост клиента: та же очередь публикации и те же метрики."""
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise RuntimeError("Аккаунт Threads не подключён.")
        post = Post(
            account_id=account.id,
            source=PostSource.client_own,
            text=text,
            status=PostStatus.draft,
        )
        session.add(post)
        session.flush()
        return post.id


def build_stats(days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            return "Акаунт Threads не підключено."

        posts = session.scalars(
            select(Post).where(
                Post.account_id == account.id,
                Post.status == PostStatus.published,
                Post.published_at >= since,
            )
        ).all()

        total_views = total_inter = 0
        by_arch: dict[Archetype, dict] = {}
        scored = []
        for post in posts:
            m = _latest_metric(post)
            if m is None:
                continue
            inter = m.likes + m.replies + m.reposts
            total_views += m.views
            total_inter += inter
            scored.append((post, m))
            if post.archetype:
                agg = by_arch.setdefault(post.archetype, {"n": 0, "views": 0, "er": 0.0})
                agg["n"] += 1
                agg["views"] += m.views
                agg["er"] += m.engagement_rate

        followers = session.scalar(
            select(AccountMetric.followers_count)
            .where(AccountMetric.account_id == account.id)
            .order_by(AccountMetric.fetched_at.desc())
            .limit(1)
        )

        # просмотры на уровне АККАУНТА за период (то, что клиент видит в Threads —
        # охват ВСЕХ его постов в окне, а не только опубликованных через систему)
        account_views = None
        try:
            from app.crypto import decrypt_token
            from app.threads import client as threads

            token = decrypt_token(account.access_token_encrypted)
            until = int(datetime.now(timezone.utc).timestamp())
            account_views = threads.get_account_views(
                token, account.threads_user_id, int(since.timestamp()), until
            )
        except Exception:
            account_views = None  # инсайт недоступен — не рушим статистику

        lines = [f"📊 <b>Статистика за {days} дн.</b>", ""]
        if account_views is not None:
            lines.append(f"👁 <b>Перегляди акаунту: {account_views:,}</b>".replace(",", " "))
            lines.append("")
        lines.append(f"— по постах акаунту за період ({len(posts)}):")
        lines.append(f"Перегляди цих постів: {total_views:,}".replace(",", " "))
        lines.append(f"Взаємодії: {total_inter}")
        if total_views:
            lines.append(f"Середній ER: {total_inter / total_views:.2%}")
        if followers is not None:
            lines.append(f"Підписники зараз: {followers}")

        if by_arch:
            lines += ["", "<b>По архетипах:</b>"]
            for arch, agg in sorted(
                by_arch.items(), key=lambda kv: kv[1]["er"] / kv[1]["n"], reverse=True
            ):
                lines.append(
                    f"• {ARCHETYPE_LABELS.get(arch, arch.value)}: {agg['n']} пост(и), "
                    f"{agg['views']} переглядів, ER {agg['er'] / agg['n']:.2%}"
                )

        top = sorted(scored, key=lambda t: t[1].engagement_rate, reverse=True)[:3]
        if top:
            lines += ["", "<b>Топ-пости:</b>"]
            for post, m in top:
                preview = post.text[:80].replace("\n", " ")
                lines.append(f"• «{preview}…» — {m.views} переглядів, ER {m.engagement_rate:.2%}")

        return "\n".join(lines)
