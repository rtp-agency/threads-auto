"""Сохранение вычитанного voice profile в БД (ТЗ п.5, шаги 5).

Использование:
    docker compose run --rm api python scripts/save_voice_profile.py \
        --profile voice_profile.md --examples examples.json

profile — вычитанный вручную текст описания голоса;
examples — JSON-массив из 3-5 дословных постов клиента (few-shot).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import Account, VoiceProfile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, help="файл с описанием голоса")
    parser.add_argument("--examples", required=True, help="JSON-массив verbatim-постов")
    args = parser.parse_args()

    profile_text = Path(args.profile).read_text()
    examples = json.loads(Path(args.examples).read_text())
    if not isinstance(examples, list) or not (3 <= len(examples) <= 7):
        raise SystemExit("examples должен быть JSON-массивом из 3-5 постов.")

    with session_scope() as session:
        account = session.scalar(select(Account).limit(1))
        if account is None:
            raise SystemExit("Аккаунт не подключён.")
        existing = session.scalar(
            select(VoiceProfile).where(VoiceProfile.account_id == account.id)
        )
        if existing:
            existing.profile_text = profile_text
            existing.examples = examples
            print("Voice profile обновлён.")
        else:
            session.add(
                VoiceProfile(
                    account_id=account.id,
                    profile_text=profile_text,
                    examples=examples,
                )
            )
            print("Voice profile создан.")


if __name__ == "__main__":
    main()
