"""Отправка сообщений в Telegram из Celery-задач (без aiogram, простым HTTP)."""
import httpx

from app.config import settings


def send_message(text: str, chat_id: str | None = None) -> None:
    chat_id = chat_id or settings.telegram_admin_chat_id
    httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    ).raise_for_status()
