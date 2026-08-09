"""Рефреш long-lived токенов Threads до истечения (ТЗ п.1)."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.crypto import decrypt_token, encrypt_token
from app.db import session_scope
from app.models import Account
from app.tasks import celery_app
from app.threads import client as threads

logger = logging.getLogger(__name__)

# Рефрешим заранее: токен живёт 60 дней, обновляем когда осталось меньше 10.
REFRESH_THRESHOLD_DAYS = 10


@celery_app.task
def refresh_tokens() -> None:
    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=REFRESH_THRESHOLD_DAYS)
    with session_scope() as session:
        accounts = session.scalars(
            select(Account).where(Account.token_expires_at <= threshold)
        ).all()
        for account in accounts:
            try:
                token = decrypt_token(account.access_token_encrypted)
                data = threads.refresh_long_lived_token(token)
                account.access_token_encrypted = encrypt_token(data["access_token"])
                account.token_expires_at = now + timedelta(
                    seconds=data.get("expires_in", 60 * 24 * 3600)
                )
                logger.info("Refreshed token for @%s", account.username)
            except Exception:
                logger.exception("Token refresh failed for @%s", account.username)
