"""Автопостинг (ТЗ п.10): после ✅ в боте — create container -> publish.

SLA: публикация не позднее 15 минут после апрува — задача ставится в очередь
сразу по нажатию кнопки; сама публикация занимает ~30 сек (задержка контейнера).
"""
import logging
from datetime import datetime, timezone

from app.crypto import decrypt_token
from app.db import session_scope
from app.llm.embeddings import embed_one
from app.models import Account, Post, PostEmbedding, PostStatus
from app.tasks import celery_app
from app.tasks.telegram import send_message
from app.threads import client as threads

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def publish_post(self, post_id: int) -> None:
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None or post.status != PostStatus.approved:
            logger.warning("Post %s not found or not approved, skip", post_id)
            return
        account = session.get(Account, post.account_id)
        token = decrypt_token(account.access_token_encrypted)

        try:
            media_id = threads.publish_post(
                token, account.threads_user_id, post.text, post.image_url
            )
        except Exception as exc:
            logger.exception("Publish failed for post %s", post_id)
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                send_message(
                    f"❌ Не вдалося опублікувати пост #{post_id} після 3 спроб. "
                    f"Перевірте логи."
                )
                return

        post.threads_media_id = media_id
        post.status = PostStatus.published
        post.published_at = datetime.now(timezone.utc)

        # первый комментарий (перевод/ответ) — публикуем ответом к посту.
        # Закрепление (pin) через API недоступно — клиент закрепляет вручную.
        first_comment = (post.meta or {}).get("first_comment")
        if first_comment:
            try:
                threads.publish_reply(
                    token, account.threads_user_id, first_comment, media_id
                )
                send_message(
                    f"💬 Перший коментар до поста #{post_id} опубліковано. "
                    f"Закріпи його вручну (📌) — через API це недоступно."
                )
            except Exception:
                logger.exception("First comment failed for post %s", post_id)
                send_message(
                    f"⚠️ Пост #{post_id} опубліковано, але коментар не пройшов — "
                    f"додай його вручну."
                )

        # Эмбеддинг для few-shot retrieval (ТЗ п.6)
        try:
            vector = embed_one(post.text, input_type="document")
            session.merge(PostEmbedding(post_id=post.id, embedding=vector))
        except Exception:
            logger.exception("Embedding failed for post %s (non-blocking)", post_id)

        send_message(f"✅ Пост #{post_id} опубліковано в Threads.")
