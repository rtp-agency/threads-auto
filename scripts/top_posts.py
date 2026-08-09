"""Печатает топ текстовых постов по engagement для ручной вычитки примеров."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.crypto import decrypt_token  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import Account  # noqa: E402
from app.threads import client as threads  # noqa: E402


def eng(m):
    v = m.get("views") or 0
    return (m.get("likes", 0) + m.get("replies", 0) + m.get("reposts", 0)) / v if v else 0.0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    with session_scope() as s:
        acc = s.scalar(select(Account).limit(1))
        token, uid = decrypt_token(acc.access_token_encrypted), acc.threads_user_id
    posts = threads.get_all_user_threads(token, uid)
    rows = []
    for p in posts:
        t = (p.get("text") or "").strip()
        if len(t) < 40:  # отсекаем эмодзи-посты и обрывки
            continue
        m = threads.get_media_insights(token, p["id"]) if p.get("id") else {}
        rows.append((eng(m), m, t))
    rows.sort(key=lambda x: x[0], reverse=True)
    for i, (e, m, t) in enumerate(rows[:n], 1):
        print(f"\n===== #{i}  ER={e:.2%}  views={m.get('views',0)} "
              f"likes={m.get('likes',0)} replies={m.get('replies',0)} =====")
        print(t)


if __name__ == "__main__":
    main()
