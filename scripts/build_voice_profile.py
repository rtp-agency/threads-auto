"""Онбординг voice profile целиком на сервере (ТЗ п.5), без выноса данных наружу.

Читает архив постов, строит описание голоса через LLM, отбирает 3-5 verbatim
примеров с лучшим engagement и сохраняет в voice_profile. Печатает результат в
терминал для ручной вычитки (ТЗ требует контроль качества человеком).

    docker compose run --rm api python scripts/build_voice_profile.py
    docker compose run --rm api python scripts/build_voice_profile.py --dry-run

--dry-run — только показать сгенерированный профиль, не писать в БД.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.crypto import decrypt_token  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.llm.providers import structured_completion  # noqa: E402
from app.models import Account, VoiceProfile  # noqa: E402
from app.threads import client as threads  # noqa: E402

VOICE_SYSTEM = """\
Ти — лінгвіст-аналітик стилю. Тобі дають повний архів Threads-постів клієнта (текст + метрики + дата). Твоє завдання — НЕ переписувати пости,
а ОПИСАТИ його голос так, щоб інша модель за цим описом писала невідрізнювані
пости.

Опиши в profile_text (українською, детально, з дослівними прикладами слів і
зворотів автора):
- довжину і ритм речень; регістр — сленг, суржик, мат: що саме і як часто;
- структуру постів (хук -> історія -> питання до аудиторії);
- пунктуацію та оформлення (тире, дужки, капс, емодзі, переноси);
- теми і позицію автора, ставлення до аудиторії, що провокує;
- чого в голосі НЕМАЄ.

КРИТИЧНО: не згладжуй різкість, сленг і мат до ввічливого корпоративного тону.
Якщо автор пише жорстко і з матом — так і опиши, з дослівними прикладами.

В examples поклади 3-5 НАЙКРАЩИХ постів ДОСЛІВНО (verbatim, без змін) — з
найвищим залученням (лайки+коменти+репости відносно переглядів). Тільки
органічні текстові пости; пропускай пости-скріншоти/медіа без тексту."""

SCHEMA = {
    "type": "object",
    "properties": {
        "profile_text": {"type": "string"},
        "examples": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
        },
    },
    "required": ["profile_text", "examples"],
    "additionalProperties": False,
}


def _engagement(m: dict) -> float:
    views = m.get("views") or 0
    if not views:
        return 0.0
    return (m.get("likes", 0) + m.get("replies", 0) + m.get("reposts", 0)) / views


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise SystemExit("Аккаунт не подключён.")
        token = decrypt_token(account.access_token_encrypted)
        user_id = account.threads_user_id
        username = account.username
        account_id = account.id

    print(f"Тяну архив @{username}…")
    posts = threads.get_all_user_threads(token, user_id)
    print(f"Постов: {len(posts)}. Тяну метрики…")

    corpus = []
    for p in posts:
        text = (p.get("text") or "").strip()
        if not text:
            continue  # скриншоты/медиа без текста в анализ голоса не идут (ТЗ п.5)
        try:
            metrics = threads.get_media_insights(token, p["id"])
        except Exception:
            metrics = {}
        corpus.append(
            {"text": text, "date": p.get("timestamp"), "metrics": metrics,
             "engagement": round(_engagement(metrics), 4)}
        )

    corpus.sort(key=lambda x: x["engagement"], reverse=True)
    print(f"Текстовых постов для анализа: {len(corpus)}")

    payload = json.dumps(corpus, ensure_ascii=False, indent=2)
    result = structured_completion(
        model=settings.generation_model,
        system_blocks=[VOICE_SYSTEM],
        user_text=f"Архів постів (відсортовано за залученням):\n{payload}",
        schema=SCHEMA,
        max_tokens=8000,
    )

    print("\n" + "=" * 70)
    print("PROFILE_TEXT:\n")
    print(result["profile_text"])
    print("\n" + "=" * 70)
    print(f"EXAMPLES ({len(result['examples'])}):\n")
    for i, ex in enumerate(result["examples"], 1):
        print(f"[{i}] {ex}\n")
    print("=" * 70)

    if args.dry_run:
        print("\n--dry-run: в БД не записано.")
        return

    with session_scope() as session:
        existing = session.scalar(
            select(VoiceProfile).where(VoiceProfile.account_id == account_id)
        )
        if existing:
            existing.profile_text = result["profile_text"]
            existing.examples = result["examples"]
            print("\nVoice profile обновлён в БД.")
        else:
            session.add(
                VoiceProfile(
                    account_id=account_id,
                    profile_text=result["profile_text"],
                    examples=result["examples"],
                )
            )
            print("\nVoice profile создан в БД.")


if __name__ == "__main__":
    main()
