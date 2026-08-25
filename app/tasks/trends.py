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

MAX_HEADLINES = 90
PER_FEED = 25

# Тематические ленты Google News (UA) — чтобы темы были РАЗНООБРАЗНЫМИ, а не только
# спорт/футбол. settings.news_source_url остаётся первой (общая лента).
_GN = "https://news.google.com/rss/headlines/section/topic/{topic}?hl=uk&gl=UA&ceid=UA:uk"
EXTRA_FEEDS = [
    _GN.format(topic="WORLD"),
    _GN.format(topic="NATION"),
    _GN.format(topic="ENTERTAINMENT"),
    _GN.format(topic="TECHNOLOGY"),
]


@celery_app.task
def fetch_trend_topics() -> None:
    feeds = [settings.news_source_url] + EXTRA_FEEDS
    entries = []
    seen_titles = set()
    for url in feeds:
        try:
            feed = feedparser.parse(url)
        except Exception:
            logger.exception("feed parse failed: %s", url)
            continue
        for e in feed.entries[:PER_FEED]:
            title = getattr(e, "title", None)
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            entries.append(e)
        if len(entries) >= MAX_HEADLINES:
            break
    entries = entries[:MAX_HEADLINES]
    if not entries:
        logger.warning("No entries from any news feed")
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
