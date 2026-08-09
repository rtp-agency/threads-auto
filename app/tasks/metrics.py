"""Сбор метрик (ТЗ п.3): снапшоты insights по published-постам + подписчики."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.crypto import decrypt_token
from app.db import session_scope
from app.models import Account, AccountMetric, Post, PostMetric, PostStatus
from app.tasks import celery_app
from app.threads import client as threads

logger = logging.getLogger(__name__)


def compute_engagement_rate(views: int, likes: int, replies: int, reposts: int) -> float:
    """engagement_rate = (likes + replies + reposts) / views (ТЗ п.3)."""
    if not views:
        return 0.0
    return (likes + replies + reposts) / views


def sync_recent_posts(session, account, token, limit: int = 25) -> int:
    """Подтягивает РЕАЛЬНЫЕ посты аккаунта из Threads и заводит те, которых нет в
    БД (напр. опубликованные вручную, не через ✅ бота). Иначе их не было бы в
    per-post статистике и топ-постах. Идемпотентно по threads_media_id.

    Возвращает число новых записей."""
    from app.models import Post, PostSource, PostStatus

    existing = set(
        session.scalars(
            select(Post.threads_media_id).where(
                Post.account_id == account.id, Post.threads_media_id.isnot(None)
            )
        ).all()
    )
    try:
        page = threads.get_user_threads(token, account.threads_user_id, limit=limit)
    except Exception:
        logger.exception("sync_recent_posts: fetch failed for @%s", account.username)
        return 0

    added = 0
    for p in page.get("data", []):
        pid = p.get("id")
        if not pid or pid in existing:
            continue
        published_at = None
        ts = p.get("timestamp")
        if ts:
            try:
                from dateutil import parser as _dp

                published_at = _dp.parse(ts)
            except Exception:
                published_at = None
        session.add(
            Post(
                account_id=account.id,
                source=PostSource.client_own,
                threads_media_id=pid,
                text=p.get("text") or "",
                status=PostStatus.published,
                published_at=published_at,
            )
        )
        added += 1
    if added:
        session.flush()
        logger.info("sync_recent_posts: +%d real posts for @%s", added, account.username)
    return added


@celery_app.task
def collect_metrics(fresh_only: bool = False) -> None:
    """Новый снапшот в post_metrics по каждому published-посту.

    fresh_only=True — только посты, опубликованные за последние 24 часа
    (частый прогон для отслеживания скорости роста 2ч/6ч/24ч).
    """
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        accounts = session.scalars(select(Account)).all()
        for account in accounts:
            token = decrypt_token(account.access_token_encrypted)

            # сначала подтягиваем реальные посты аккаунта (в т.ч. опубликованные
            # вручную) — чтобы метрики и статистика их учитывали
            sync_recent_posts(session, account, token)

            query = select(Post).where(
                Post.account_id == account.id,
                Post.status == PostStatus.published,
                Post.threads_media_id.isnot(None),
            )
            if fresh_only:
                query = query.where(Post.published_at >= now - timedelta(hours=24))
            posts = session.scalars(query).all()

            for post in posts:
                try:
                    insights = threads.get_media_insights(token, post.threads_media_id)
                except Exception:
                    logger.exception("Insights failed for post %s", post.id)
                    continue
                views = insights.get("views", 0)
                likes = insights.get("likes", 0)
                replies = insights.get("replies", 0)
                reposts = insights.get("reposts", 0)
                session.add(
                    PostMetric(
                        post_id=post.id,
                        views=views,
                        likes=likes,
                        replies=replies,
                        reposts=reposts,
                        quotes=insights.get("quotes", 0),
                        engagement_rate=compute_engagement_rate(
                            views, likes, replies, reposts
                        ),
                    )
                )

            # Снапшот подписчиков — только при полном (суточном) прогоне
            if not fresh_only:
                try:
                    followers = threads.get_followers_count(token, account.threads_user_id)
                    session.add(
                        AccountMetric(account_id=account.id, followers_count=followers)
                    )
                except Exception:
                    logger.exception("Followers fetch failed for @%s", account.username)
