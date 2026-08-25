"""Источник трендовых мемов (meme-api.com — обёртка над популярными сабреддитами).

Бесплатно, без ключа. Возвращает свежие виральные мемы-картинки; учитель выбирает
один, а дальше работает штатный флоу подписи (generate_meme_captions)."""
import httpx

MEME_API = "https://meme-api.com/gimme"
# сабреддиты с максимально «визуальными», универсальными мемами
SUBREDDITS = ["memes", "dankmemes", "me_irl", "wholesomememes"]


def fetch_trending_memes(n: int = 5) -> list[dict]:
    """n свежих мемов: [{url, title, subreddit, ups}]. Без nsfw/spoiler."""
    try:
        resp = httpx.get(f"{MEME_API}/{max(1, min(n * 2, 20))}", timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    out = []
    for m in data.get("memes", []):
        if m.get("nsfw") or m.get("spoiler"):
            continue
        url = m.get("url", "")
        if not url.lower().endswith((".jpg", ".jpeg", ".png")):
            continue  # только прямые картинки (не gif/видео)
        out.append(
            {
                "url": url,
                "title": m.get("title", ""),
                "subreddit": m.get("subreddit", ""),
                "ups": m.get("ups", 0),
            }
        )
        if len(out) >= n:
            break
    return out


def download_image(url: str) -> bytes:
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.content
