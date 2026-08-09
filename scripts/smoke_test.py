"""Смоук-тест: проверяет компоненты, генерирует примеры постов голосом учителя
и присылает их в Telegram-чат клиента с картинками.

    docker compose run --rm api python scripts/smoke_test.py [N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.images.generator import generate_notification_image  # noqa: E402
from app.llm.generation import generate_drafts  # noqa: E402
from app.models import Account, AvatarPool, StyleReference, VoiceProfile  # noqa: E402

TG = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
CHAT = settings.telegram_admin_chat_id

TITLES = {
    "pov_confession": "POV / скріншот",
    "educational": "Навчальний",
    "trend_reaction": "Реакція на тренд",
    "meme_notification": "Мем",
}


def send_message(text: str) -> None:
    httpx.post(f"{TG}/sendMessage", json={"chat_id": CHAT, "text": text, "parse_mode": "HTML"}, timeout=30)


def send_photo(path: Path, caption: str) -> None:
    with open(path, "rb") as f:
        httpx.post(
            f"{TG}/sendPhoto",
            data={"chat_id": CHAT, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"photo": f},
            timeout=60,
        )


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    # --- 1. проверки компонентов ---
    with session_scope() as s:
        acc = s.scalar(select(Account).limit(1))
        vp = s.scalar(select(VoiceProfile).limit(1))
        refs = s.scalar(select(func.count()).select_from(StyleReference))
        avs = s.scalar(select(func.count()).select_from(AvatarPool))
    problems = []
    if acc is None:
        problems.append("немає підключеного акаунта")
    if vp is None:
        problems.append("немає voice profile")
    if not avs:
        problems.append("порожній пул аватарок")

    checks = (
        f"🧪 <b>Smoke-тест</b>\n\n"
        f"Акаунт: @{acc.username if acc else '—'}\n"
        f"Voice profile: {'✅' if vp else '❌'}\n"
        f"Референси стилю: {refs}\n"
        f"Аватарки: {avs}\n"
        f"Текст: {settings.llm_provider} / {settings.generation_model}\n"
        f"Картинки: {settings.notification_render_mode} / {settings.image_model}\n"
    )
    if problems:
        checks += "\n⚠️ " + "; ".join(problems)
    send_message(checks)
    if acc is None or vp is None:
        send_message("❌ Смоук зупинено: не вистачає базових даних (див. вище).")
        return

    send_message(f"Генерую {n} прикладів голосом клієнта…")

    # --- 2. генерация примеров (без записи в БД — это превью) ---
    with session_scope() as s:
        drafts = generate_drafts(s, acc.id, n_drafts=n)

    # --- 3. рендер картинок + отправка в чат ---
    for d in drafts:
        title = TITLES.get(d["archetype"], d["archetype"])
        caption = f"<b>[{title}]</b>\n\n{d['text']}"
        notif = d.get("notification")
        if notif and d["archetype"] == "pov_confession":
            try:
                with session_scope() as s:
                    url = generate_notification_image(
                        s, notif["app"], notif["sender"], notif["message"], notif.get("amount")
                    )
                path = Path(settings.media_dir) / url.rsplit("/", 1)[-1]
                send_photo(path, caption)
                continue
            except Exception as exc:
                caption += f"\n\n⚠️ фото не згенерувалось: {exc}"
        send_message(caption)

    send_message("✅ Smoke-тест завершено. Це приклади — оціни тон і картинки.")
    print("smoke: sent", len(drafts), "drafts to chat", CHAT)


if __name__ == "__main__":
    main()
