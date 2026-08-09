"""Источник актуальных тем (ТЗ п.4): RSS -> LLM-фильтр (Haiku) -> trend_topics."""
import logging

import feedparser
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.llm.trends import filter_headlines
from app.models import TrendTopic
from app.tasks import celery_app

logger = logging.getLogger(__name__)

MAX_HEADLINES = 60


@celery_app.task
def fetch_trend_topics() -> None:
    feed = feedparser.parse(settings.news_source_url)
    entries = feed.entries[:MAX_HEADLINES]
    if not entries:
        logger.warning("No entries from news source %s", settings.news_source_url)
        return

    headlines = [e.title for e in entries]

    with session_scope() as session:
        # не дублируем заголовки, которые уже есть в базе
        existing = set(
            session.scalars(
                select(TrendTopic.headline).where(TrendTopic.headline.in_(headlines))
            ).all()
        )
        fresh_indices = [i for i, h in enumerate(headlines) if h not in existing]
        fresh_headlines = [headlines[i] for i in fresh_indices]
        if not fresh_headlines:
            return

        selected = filter_headlines(fresh_headlines, max_selected=5)
        for item in selected:
            entry = entries[fresh_indices[item["index"]]]
            session.add(
                TrendTopic(
                    headline=entry.title,
                    source_url=getattr(entry, "link", None),
                    relevance_score=item["relevance_score"],
                    used=False,
                )
            )
        logger.info("Saved %d trend topics", len(selected))
