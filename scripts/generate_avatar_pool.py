"""Разовая генерация пула синтетических аватаров (ТЗ п.8).

    docker compose run --rm api python scripts/generate_avatar_pool.py --count 18
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session_scope  # noqa: E402
from app.images.generator import generate_avatar_pool  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=18)
    args = parser.parse_args()

    with session_scope() as session:
        urls = generate_avatar_pool(session, count=args.count)
    print(f"Сгенерировано {len(urls)} аватаров:")
    for u in urls:
        print(" ", u)


if __name__ == "__main__":
    main()
