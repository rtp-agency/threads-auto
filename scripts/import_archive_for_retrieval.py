"""Импорт реального архива постов клиента в БД для few-shot retrieval (ТЗ п.6).

Без этого retrieval пуст: система ищет похожие посты среди status='published',
а их нет. Заливаем реальный архив (текст + метрики + эмбеддинг), чтобы под
каждую генерацию подтягивались его самые похожие топ-посты как few-shot.

Идемпотентно: посты, уже импортированные по threads_media_id, пропускаются.

    docker compose run --rm api python scripts/import_archive_for_retrieval.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dateutil import parser as dateparser  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.crypto import decrypt_token  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.llm.embeddings import embed  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    Post,
    PostEmbedding,
    PostMetric,
    PostSource,
    PostStatus,
)
from app.threads import client as threads  # noqa: E402


def _eng(m: dict) -> float:
    v = m.get("views") or 0
    return (m.get("likes", 0) + m.get("replies", 0) + m.get("reposts", 0)) / v if v else 0.0


def main() -> None:
    with session_scope() as session:
        acc = session.scalar(select(Account).limit(1))
        if acc is None:
            raise SystemExit("Аккаунт не подключён.")
        token, uid, account_id = decrypt_token(acc.access_token_encrypted), acc.threads_user_id, acc.id
        existing = set(
            session.scalars(
                select(Post.threads_media_id).where(Post.account_id == account_id)
            ).all()
        )

    print("Тяну архив…")
    posts = threads.get_all_user_threads(token, uid)
    to_import = []
    for p in posts:
        text = (p.get("text") or "").strip()
        pid = p.get("id")
        if len(text) < 20 or not pid or pid in existing:
            continue
        try:
            metrics = threads.get_media_insights(token, pid)
        except Exception:
            metrics = {}
        published_at = None
        if p.get("timestamp"):
            try:
                published_at = dateparser.parse(p["timestamp"])
            except Exception:
                published_at = None
        to_import.append((pid, text, metrics, published_at))

    print(f"К импорту: {len(to_import)} постов. Считаю эмбеддинги…")
    if not to_import:
        print("Нечего импортировать.")
        return

    # эмбеддинги батчами
    texts = [t for _, t, _, _ in to_import]
    vectors = []
    B = 100
    for i in range(0, len(texts), B):
        vectors.extend(embed(texts[i : i + B], input_type="document"))

    with session_scope() as session:
        for (pid, text, m, published_at), vec in zip(to_import, vectors):
            post = Post(
                account_id=account_id,
                threads_media_id=pid,
                source=PostSource.client_own,
                archetype=None,  # архетип исторических постов не размечаем
                text=text,
                status=PostStatus.published,
                published_at=published_at,
            )
            session.add(post)
            session.flush()
            session.add(
                PostMetric(
                    post_id=post.id,
                    views=m.get("views", 0),
                    likes=m.get("likes", 0),
                    replies=m.get("replies", 0),
                    reposts=m.get("reposts", 0),
                    quotes=m.get("quotes", 0),
                    engagement_rate=_eng(m),
                    fetched_at=published_at or datetime.utcnow(),
                )
            )
            session.add(PostEmbedding(post_id=post.id, embedding=vec))
    print(f"Импортировано {len(to_import)} постов с метриками и эмбеддингами.")


if __name__ == "__main__":
    main()
