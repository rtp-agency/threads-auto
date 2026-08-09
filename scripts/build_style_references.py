"""Отбор реальных скриншотов клиента как референсов стиля для генерации фото.

Проходит по картинкам архива, vision-моделью определяет, какие из них —
скриншоты-мокапы (Telegram/SMS/уведомление), и сохраняет лучшие в
style_references (в БД, base64, ужато). Затем генерация meme_notification
делает картинки 'в их стиле'.

    docker compose run --rm api python scripts/build_style_references.py --max 6
"""
import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.crypto import decrypt_token  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.images.generator import add_style_reference  # noqa: E402
from app.models import Account, StyleReference  # noqa: E402
from app.threads import client as threads  # noqa: E402

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

VISION_SCHEMA = {
    "type": "object",
    "properties": {"is_screenshot": {"type": "boolean"}},
    "required": ["is_screenshot"],
    "additionalProperties": False,
}


def _eng(m: dict) -> float:
    v = m.get("views") or 0
    return (m.get("likes", 0) + m.get("replies", 0) + m.get("reposts", 0)) / v if v else 0.0


def is_screenshot(img_bytes: bytes) -> bool:
    """True, если картинка — скриншот-мокап переписки/уведомления.

    Картинку передаём base64 data-URL: OpenAI не может качать Threads/IG CDN.
    """
    data_url = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()
    resp = httpx.post(
        OPENAI_CHAT_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            # классификация — фиксированная OpenAI vision-модель (не зависит от LLM_PROVIDER)
            "model": "gpt-5-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Це скріншот-мокап переписки або сповіщення "
                            "(Telegram/SMS/monobank/push)? Відповідай JSON "
                            "{is_screenshot: bool}. true лише якщо це імітація "
                            "екрана телефону з повідомленням, а не мем чи фото.",
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "r", "strict": True, "schema": VISION_SCHEMA},
            },
            "max_completion_tokens": 2000,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])["is_screenshot"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=6, help="сколько референсов сохранить")
    parser.add_argument("--replace", action="store_true", help="очистить архивные референсы")
    args = parser.parse_args()

    with session_scope() as session:
        acc = session.scalar(select(Account).limit(1))
        if acc is None:
            raise SystemExit("Аккаунт не подключён.")
        token, uid = decrypt_token(acc.access_token_encrypted), acc.threads_user_id

    posts = threads.get_all_user_threads(token, uid)
    imgs = [p for p in posts if p.get("media_type") == "IMAGE" and p.get("media_url")]
    print(f"Картинок в архиве: {len(imgs)}. Классифицирую…")

    # оцениваем по engagement, чтобы брать лучшие скриншоты
    scored = []
    for p in imgs:
        try:
            img_bytes = httpx.get(p["media_url"], timeout=60).content
        except Exception as exc:
            print("  download error:", exc)
            continue
        try:
            if not is_screenshot(img_bytes):
                continue
        except Exception as exc:
            print("  vision error:", exc)
            continue
        try:
            m = threads.get_media_insights(token, p["id"])
        except Exception:
            m = {}
        scored.append((_eng(m), img_bytes))
        print(f"  скриншот ✓ ER={_eng(m):.2%} {(p.get('text') or '')[:40]}")

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = scored[: args.max]
    if not chosen:
        print("Скриншотов-мокапов не найдено.")
        return

    with session_scope() as session:
        if args.replace:
            session.execute(delete(StyleReference).where(StyleReference.source == "archive"))
        for _, img_bytes in chosen:
            add_style_reference(session, img_bytes, source="archive")
    print(f"Сохранено референсов стиля: {len(chosen)}")


if __name__ == "__main__":
    main()
