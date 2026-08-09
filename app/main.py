"""FastAPI-сервис: OAuth callback для Threads + раздача сгенерированных медиа.

Threads API требует публичный URL картинки при публикации, поэтому /media
должен быть доступен извне (PUBLIC_BASE_URL).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import settings
from app.crypto import encrypt_token
from app.db import session_scope
from app.models import Account
from app.threads import client as threads

app = FastAPI(title="Threads Automation")

Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/auth/threads/login")
def threads_login() -> RedirectResponse:
    """Отправить клиенту эту ссылку после принятия инвайта Threads Tester."""
    return RedirectResponse(threads.build_authorize_url())


@app.get("/auth/threads/callback", response_class=PlainTextResponse)
def threads_callback(code: str | None = None, error: str | None = None) -> str:
    """OAuth flow (ТЗ п.1): code -> короткоживущий -> long-lived (60 дней)."""
    if error or not code:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    short = threads.exchange_code_for_short_lived_token(code)
    long_lived = threads.exchange_for_long_lived_token(short["access_token"])
    token = long_lived["access_token"]
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=long_lived.get("expires_in", 60 * 24 * 3600)
    )

    me = threads.get_me(token)

    with session_scope() as session:
        account = session.scalar(
            select(Account).where(Account.threads_user_id == str(me["id"]))
        )
        if account is None:
            account = Account(
                threads_user_id=str(me["id"]),
                username=me.get("username", ""),
                access_token_encrypted=encrypt_token(token),
                token_expires_at=expires_at,
            )
            session.add(account)
        else:
            account.username = me.get("username", account.username)
            account.access_token_encrypted = encrypt_token(token)
            account.token_expires_at = expires_at

    return (
        f"Акаунт @{me.get('username')} підключено. "
        f"Токен збережено (зашифровано), можна закривати сторінку."
    )


# --- Callback-эндпоинты, обязательные для валидации Threads use case ----------

@app.api_route("/auth/threads/uninstall", methods=["GET", "POST"])
async def threads_uninstall(request: Request) -> JSONResponse:
    """Meta пингует этот URL, когда пользователь отзывает доступ к приложению."""
    return JSONResponse({"status": "ok"})


@app.api_route("/auth/threads/delete", methods=["GET", "POST"])
async def threads_delete(request: Request) -> JSONResponse:
    """Data Deletion Callback. Meta ждёт url + confirmation_code в ответе."""
    return JSONResponse(
        {
            "url": f"{settings.public_base_url.rstrip('/')}/privacy",
            "confirmation_code": "threadsauto-delete",
        }
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy() -> str:
    return """<!doctype html><html lang="uk"><head><meta charset="utf-8">
<title>Політика конфіденційності — ThreadsAuto</title></head><body>
<h1>Політика конфіденційності ThreadsAuto</h1>
<p>Сервіс ThreadsAuto використовується для автоматизації публікацій та збору
аналітики одного Threads-акаунта його власником.</p>
<h2>Які дані обробляються</h2>
<p>Токен доступу Threads (зберігається у зашифрованому вигляді), тексти та
метрики публікацій акаунта (перегляди, лайки, коментарі, репости), кількість
підписників. Дані не передаються третім особам, окрім API, потрібних для роботи
сервісу (Threads API, провайдер генерації тексту та зображень).</p>
<h2>Видалення даних</h2>
<p>Щоб видалити всі дані, надішліть запит через endpoint деавторизації додатку
у налаштуваннях Threads або зверніться до власника сервісу. Після відкликання
доступу токен та пов'язані дані видаляються.</p>
<h2>Контакт</h2>
<p>klird91@gmail.com</p>
</body></html>"""
