"""Клиент Threads API (graph.threads.net).

Auth-модель — Threads Tester (ТЗ п.1): клиент добавлен тестером в Meta-приложении,
все permissions доступны без App Review.

Permissions: threads_basic, threads_content_publish, threads_manage_insights.
"""
import time
from urllib.parse import urlencode

import httpx

from app.config import settings

GRAPH = "https://graph.threads.net"
API = f"{GRAPH}/v1.0"

SCOPES = "threads_basic,threads_content_publish,threads_manage_insights"

# Задержка между create container и publish (рекомендация Meta ~30 сек,
# чтобы сервер успел обработать контейнер).
PUBLISH_DELAY_SECONDS = 30

POST_INSIGHT_METRICS = "views,likes,replies,reposts,quotes"


class ThreadsAPIError(RuntimeError):
    pass


def _get(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, timeout=30)
    return _handle(resp)


def _post(url: str, params: dict) -> dict:
    resp = httpx.post(url, params=params, timeout=30)
    return _handle(resp)


def _handle(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise ThreadsAPIError(f"Non-JSON response: {resp.text[:200]}")
    if resp.status_code >= 400 or "error" in data:
        raise ThreadsAPIError(f"Threads API error {resp.status_code}: {data}")
    return data


# --- OAuth -------------------------------------------------------------------

def build_authorize_url() -> str:
    """Ссылка авторизации — отдать клиенту после принятия инвайта Threads Tester."""
    params = {
        "client_id": settings.threads_app_id,
        "redirect_uri": settings.threads_redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
    }
    return f"https://threads.net/oauth/authorize?{urlencode(params)}"


def exchange_code_for_short_lived_token(code: str) -> dict:
    """code -> короткоживущий токен (~1 час)."""
    return _post(
        f"{GRAPH}/oauth/access_token",
        {
            "client_id": settings.threads_app_id,
            "client_secret": settings.threads_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": settings.threads_redirect_uri,
            "code": code,
        },
    )


def exchange_for_long_lived_token(short_lived_token: str) -> dict:
    """Короткоживущий -> long-lived (60 дней). Возвращает access_token, expires_in."""
    return _get(
        f"{GRAPH}/access_token",
        {
            "grant_type": "th_exchange_token",
            "client_secret": settings.threads_app_secret,
            "access_token": short_lived_token,
        },
    )


def refresh_long_lived_token(token: str) -> dict:
    """Рефреш long-lived токена (продлевает ещё на 60 дней)."""
    return _get(
        f"{GRAPH}/refresh_access_token",
        {"grant_type": "th_refresh_token", "access_token": token},
    )


# --- Профиль и посты ---------------------------------------------------------

def get_me(token: str) -> dict:
    return _get(f"{API}/me", {"fields": "id,username", "access_token": token})


def get_user_threads(token: str, user_id: str, limit: int = 100, after: str | None = None) -> dict:
    """Список публикаций пользователя (одна страница; пагинация через after)."""
    params = {
        "fields": "id,text,timestamp,media_type,media_url,permalink",
        "limit": limit,
        "access_token": token,
    }
    if after:
        params["after"] = after
    return _get(f"{API}/{user_id}/threads", params)


def get_all_user_threads(token: str, user_id: str) -> list[dict]:
    """Весь архив постов (для voice profile, ТЗ п.5)."""
    posts: list[dict] = []
    after = None
    while True:
        page = get_user_threads(token, user_id, after=after)
        posts.extend(page.get("data", []))
        after = page.get("paging", {}).get("cursors", {}).get("after")
        if not after or not page.get("data"):
            break
    return posts


# --- Insights ----------------------------------------------------------------

def get_media_insights(token: str, media_id: str) -> dict[str, int]:
    """Метрики поста: views/likes/replies/reposts/quotes."""
    data = _get(
        f"{API}/{media_id}/insights",
        {"metric": POST_INSIGHT_METRICS, "access_token": token},
    )
    result: dict[str, int] = {}
    for item in data.get("data", []):
        values = item.get("values") or [{}]
        result[item["name"]] = values[0].get("value", 0)
    return result


def get_followers_count(token: str, user_id: str) -> int:
    data = _get(
        f"{API}/{user_id}/threads_insights",
        {"metric": "followers_count", "access_token": token},
    )
    for item in data.get("data", []):
        if item["name"] == "followers_count":
            if item.get("total_value") is not None:
                return item["total_value"].get("value", 0)
            values = item.get("values") or [{}]
            return values[0].get("value", 0)
    return 0


def get_account_views(token: str, user_id: str, since: int, until: int) -> int:
    """Просмотры на уровне АККАУНТА за период [since; until] (Unix-время).

    Это то число, что видит клиент в Threads («перегляди за 7 днів») — суммарный
    охват всех его постов в окне, а не только опубликованных через систему."""
    data = _get(
        f"{API}/{user_id}/threads_insights",
        {
            "metric": "views",
            "since": since,
            "until": until,
            "access_token": token,
        },
    )
    for item in data.get("data", []):
        if item["name"] == "views":
            if item.get("total_value") is not None:
                return item["total_value"].get("value", 0)
            # иначе — временной ряд по дням: суммируем
            return sum(v.get("value", 0) for v in (item.get("values") or []))
    return 0


# --- Публикация (двухшаговый flow) --------------------------------------------

def create_container(
    token: str,
    user_id: str,
    text: str,
    image_url: str | None = None,
    reply_to_id: str | None = None,
) -> str:
    """Шаг 1: создать media container. Возвращает creation_id.

    reply_to_id — если задан, это ответ (комментарий) к указанному посту."""
    params: dict = {"access_token": token, "text": text}
    if image_url:
        params["media_type"] = "IMAGE"
        params["image_url"] = image_url
    else:
        params["media_type"] = "TEXT"
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    data = _post(f"{API}/{user_id}/threads", params)
    return data["id"]


def publish_container(token: str, user_id: str, creation_id: str) -> str:
    """Шаг 2: опубликовать контейнер. Возвращает threads_media_id."""
    data = _post(
        f"{API}/{user_id}/threads_publish",
        {"access_token": token, "creation_id": creation_id},
    )
    return data["id"]


def publish_post(token: str, user_id: str, text: str, image_url: str | None = None) -> str:
    """Полный flow: create container -> задержка ~30 сек -> publish."""
    creation_id = create_container(token, user_id, text, image_url)
    time.sleep(PUBLISH_DELAY_SECONDS)
    return publish_container(token, user_id, creation_id)


def publish_reply(token: str, user_id: str, text: str, reply_to_id: str) -> str:
    """Опубликовать ответ (комментарий) к посту reply_to_id. Возвращает media_id
    ответа. Прикрепление (pin) комментария в Threads API не поддерживается —
    закрепляется вручную."""
    creation_id = create_container(token, user_id, text, reply_to_id=reply_to_id)
    time.sleep(PUBLISH_DELAY_SECONDS)
    return publish_container(token, user_id, creation_id)
