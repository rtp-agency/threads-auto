"""Разовый скрипт онбординга (ТЗ п.5, шаг 1): выгрузка ВСЕГО архива постов
клиента через Threads API в структурированный JSON — текст + метрики + дата.

НЕ часть production-пайплайна. Запускается вручную через Claude Code после
получения Threads Tester доступа:

    docker compose run --rm api python scripts/fetch_threads_archive.py

Результат: threads_archive.json — скормить Claude вместе с промптом из
docs/voice_profile_prompt.md, вычитать результат и сохранить его через
scripts/save_voice_profile.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.crypto import decrypt_token  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import Account  # noqa: E402
from app.threads import client as threads  # noqa: E402

OUTPUT = Path("threads_archive.json")


def main() -> None:
    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise SystemExit("Аккаунт не подключён — сначала пройдите OAuth flow.")
        token = decrypt_token(account.access_token_encrypted)
        user_id = account.threads_user_id
        username = account.username

    print(f"Выгружаю архив @{username}…")
    posts = threads.get_all_user_threads(token, user_id)
    print(f"Постов: {len(posts)}. Тяну метрики (может занять время)…")

    archive = []
    for i, p in enumerate(posts, 1):
        item = {
            "id": p.get("id"),
            "date": p.get("timestamp"),
            "media_type": p.get("media_type"),
            "text": p.get("text", ""),
            "permalink": p.get("permalink"),
        }
        try:
            item["metrics"] = threads.get_media_insights(token, p["id"])
        except Exception as exc:
            item["metrics"] = {"error": str(exc)}
        archive.append(item)
        if i % 20 == 0:
            print(f"  {i}/{len(posts)}")

    OUTPUT.write_text(json.dumps(archive, ensure_ascii=False, indent=2))
    print(f"Готово: {OUTPUT} ({len(archive)} постов).")
    print("Дальше: docs/voice_profile_prompt.md -> Claude -> вычитка -> "
          "scripts/save_voice_profile.py")


if __name__ == "__main__":
    main()
