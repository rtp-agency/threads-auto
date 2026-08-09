from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "threads_automation",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.tokens",
        "app.tasks.metrics",
        "app.tasks.trends",
        "app.tasks.publish",
        "app.tasks.reports",
    ],
)

celery_app.conf.timezone = "Europe/Kyiv"

celery_app.conf.beat_schedule = {
    # Рефреш long-lived токенов до истечения (ТЗ п.1)
    "refresh-threads-tokens": {
        "task": "app.tasks.tokens.refresh_tokens",
        "schedule": crontab(hour=3, minute=0),
    },
    # Полный сбор метрик — не реже раза в сутки (ТЗ п.3)
    "collect-metrics-daily": {
        "task": "app.tasks.metrics.collect_metrics",
        "schedule": crontab(hour=6, minute=0),
        "kwargs": {"fresh_only": False},
    },
    # Свежие посты (<24ч) — каждые 2 часа, для скорости роста 2ч/6ч/24ч
    "collect-metrics-fresh": {
        "task": "app.tasks.metrics.collect_metrics",
        "schedule": crontab(minute=0, hour="*/2"),
        "kwargs": {"fresh_only": True},
    },
    # Сбор и LLM-фильтрация инфоповодов — раз в день (ТЗ п.4)
    "fetch-trend-topics": {
        "task": "app.tasks.trends.fetch_trend_topics",
        "schedule": crontab(hour=8, minute=0),
    },
    # Еженедельный отчёт (ТЗ п.11)
    "weekly-report": {
        "task": "app.tasks.reports.send_weekly_report",
        "schedule": crontab(
            day_of_week=settings.report_weekday,
            hour=settings.report_hour,
            minute=0,
        ),
    },
}
