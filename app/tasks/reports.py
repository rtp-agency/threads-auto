"""Еженедельный отчёт (ТЗ п.11): просмотры, вовлечённость, рост подписчиков,
сравнение перформанса по архетипам. Проактивно отправляется ботом."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Account, AccountMetric, Archetype, Post, PostStatus
from app.tasks import celery_app
from app.tasks.telegram import send_message

logger = logging.getLogger(__name__)

ARCHETYPE_LABELS = {
    Archetype.pov_confession: "POV/скріншот",
    Archetype.educational: "Навчальний",
    Archetype.trend_reaction: "Реакція на тренд",
    Archetype.meme_notification: "Мем",
    Archetype.transformation: "Трансформація",
    Archetype.pain_point: "Біль→рішення",
    Archetype.lead_magnet: "Лід-магніт",
}


def _latest_metric(post: Post):
    return max(post.metrics, key=lambda m: m.fetched_at, default=None)


def build_report(session, account: Account, days: int = 7) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    posts = session.scalars(
        select(Post).where(
            Post.account_id == account.id,
            Post.status == PostStatus.published,
            Post.published_at >= since,
        )
    ).all()

    total_views = total_inter = 0
    by_archetype: dict[Archetype, dict] = {}
    scored: list[tuple[Post, float, int]] = []
    total_leads = 0

    for post in posts:
        m = _latest_metric(post)
        if m is None:
            continue
        interactions = m.likes + m.replies + m.reposts
        total_views += m.views
        total_inter += interactions
        if post.brought_lead:
            total_leads += 1
        scored.append((post, m.engagement_rate, m.views))
        if post.archetype:
            agg = by_archetype.setdefault(
                post.archetype,
                {"posts": 0, "views": 0, "er_sum": 0.0, "replies": 0, "reposts": 0, "leads": 0},
            )
            agg["posts"] += 1
            agg["views"] += m.views
            agg["er_sum"] += m.engagement_rate
            agg["replies"] += m.replies
            agg["reposts"] += m.reposts
            agg["leads"] += 1 if post.brought_lead else 0

    # рост подписчиков за период
    followers_now = session.scalar(
        select(AccountMetric.followers_count)
        .where(AccountMetric.account_id == account.id)
        .order_by(AccountMetric.fetched_at.desc())
        .limit(1)
    )
    followers_then = session.scalar(
        select(AccountMetric.followers_count)
        .where(
            AccountMetric.account_id == account.id,
            AccountMetric.fetched_at <= since,
        )
        .order_by(AccountMetric.fetched_at.desc())
        .limit(1)
    )

    lines = [f"📊 <b>Тижневий звіт @{account.username}</b>", ""]
    lines.append(f"Постів опубліковано: {len(posts)}")
    lines.append(f"Перегляди: {total_views}")
    lines.append(f"Взаємодії (лайки+коменти+репости): {total_inter}")
    if total_views:
        lines.append(f"Середній ER: {total_inter / total_views:.2%}")
    if followers_now is not None:
        growth = ""
        if followers_then is not None:
            delta = followers_now - followers_then
            growth = f" ({'+' if delta >= 0 else ''}{delta} за тиждень)"
        lines.append(f"Підписники: {followers_now}{growth}")

    lines.append(f"Запитів/лідів (позначено вручну): {total_leads}")

    if by_archetype:
        lines.append("")
        lines.append("<b>Порівняння форматів</b> (охоплення = коменти+репости):")
        # сортируем по комментам+репостам — это прокси охвата в Threads
        for arch, agg in sorted(
            by_archetype.items(),
            key=lambda kv: (kv[1]["replies"] + kv[1]["reposts"]) / kv[1]["posts"],
            reverse=True,
        ):
            n = agg["posts"]
            reach = (agg["replies"] + agg["reposts"]) / n
            lead = f", 🎯{agg['leads']} лід(и)" if agg["leads"] else ""
            lines.append(
                f"• {ARCHETYPE_LABELS.get(arch, arch.value)}: {n} пост(и), "
                f"{agg['views']} переглядів, ~{reach:.0f} коментів+репостів/пост, "
                f"ER {agg['er_sum'] / n:.2%}{lead}"
            )

    top = sorted(scored, key=lambda t: t[1], reverse=True)[:3]
    if top:
        lines.append("")
        lines.append("<b>Топ-пости тижня:</b>")
        for post, er, views in top:
            preview = post.text[:80].replace("\n", " ")
            lines.append(f"• «{preview}…» — {views} переглядів, ER {er:.2%}")

    return "\n".join(lines)


@celery_app.task
def send_weekly_report() -> None:
    with session_scope() as session:
        accounts = session.scalars(select(Account)).all()
        for account in accounts:
            try:
                send_message(build_report(session, account))
            except Exception:
                logger.exception("Weekly report failed for @%s", account.username)
