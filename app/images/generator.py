"""Генерация изображений для архетипа meme_notification (ТЗ п.8).

- Скриншот-мокап уведомления (Telegram/monobank/SMS, стиль iOS lock screen).
- Пул синтетических аватаров генерируется разово (15-20 шт), при генерации
  поста берётся случайный из avatar_pool.
- Автовалидации читаемости текста нет (вне скоупа MVP) — только ручная
  перегенерация кнопкой в боте.

Провайдер: openai (gpt-image-1) или gemini — переключается IMAGE_GEN_PROVIDER.
Картинки сохраняются в MEDIA_DIR и отдаются FastAPI по /media/... —
Threads API требует публичный URL изображения.
"""
import base64
import io
import random
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AvatarPool, StyleReference

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
OPENAI_IMAGES_EDIT_URL = "https://api.openai.com/v1/images/edits"
GEMINI_IMAGES_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash-preview-image-generation:generateContent"
)

# сколько референсов подмешивать в генерацию (больше = дороже/медленнее)
MAX_STYLE_REFS = 2


def compress_for_reference(image_bytes: bytes, max_side: int = 1024) -> str:
    """Ужимает картинку до max_side по длинной стороне -> PNG base64 (для БД)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def add_style_reference(session: Session, image_bytes: bytes, source: str) -> None:
    session.add(
        StyleReference(image_b64=compress_for_reference(image_bytes), source=source, active=True)
    )


def _load_style_refs(
    session: Session, limit: int = MAX_STYLE_REFS, kind: str = "telegram"
) -> list[bytes]:
    """kind='monobank' -> референсы monobank-переказа; иначе — Telegram-скрины."""
    q = select(StyleReference.image_b64).where(StyleReference.active.is_(True))
    if kind == "monobank":
        q = q.where(StyleReference.source == "monobank")
    else:
        q = q.where(StyleReference.source != "monobank")
    rows = session.scalars(q.order_by(func.random()).limit(limit)).all()
    return [base64.b64decode(b) for b in rows]


def _save_image(image_bytes: bytes, prefix: str) -> str:
    """Сохраняет картинку в MEDIA_DIR, возвращает публичный URL."""
    media_dir = Path(settings.media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{uuid.uuid4().hex}.png"
    (media_dir / filename).write_bytes(image_bytes)
    return f"{settings.public_base_url.rstrip('/')}/media/{filename}"


class ImageQuotaError(RuntimeError):
    """Закончились кредиты у провайдера генерации картинок — не ретраим."""


def _image_post_retry(do_post, attempts: int = 5) -> bytes:
    """POST к image-API с ретраями на 429/5xx (иначе картинка падает на шаблон).

    Исключение — insufficient_quota (нет кредитов): это не транзиентная ошибка,
    сразу поднимаем понятную ImageQuotaError, без бессмысленных ретраев."""
    resp = None
    for i in range(attempts):
        resp = do_post()
        if resp.status_code == 429 and "insufficient_quota" in resp.text:
            raise ImageQuotaError(
                "Закінчились кредити на генерацію фото (OpenAI). Поповни баланс: "
                "platform.openai.com/settings/organization/billing."
            )
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = float(resp.headers.get("retry-after", 0) or 2 ** i)
            time.sleep(min(max(wait, 2), 30))
            continue
        resp.raise_for_status()
        return base64.b64decode(resp.json()["data"][0]["b64_json"])
    resp.raise_for_status()
    return base64.b64decode(resp.json()["data"][0]["b64_json"])


def _generate_openai(prompt: str) -> bytes:
    return _image_post_retry(
        lambda: httpx.post(
            OPENAI_IMAGES_URL,
            headers={"Authorization": f"Bearer {settings.image_gen_api_key}"},
            json={"model": settings.image_model, "prompt": prompt, "size": "1024x1024"},
            timeout=180,
        )
    )


def _generate_openai_with_refs(
    prompt: str, refs: list[bytes], size: str = "1536x1024", quality: str = "high"
) -> bytes:
    """Генерация 'в стиле референсов' через images.edit (multipart).
    По умолчанию широкий 1536x1024 (ближе к его формату) и высокое качество."""

    def do_post():
        files = [("image[]", (f"ref{i}.png", r, "image/png")) for i, r in enumerate(refs)]
        return httpx.post(
            OPENAI_IMAGES_EDIT_URL,
            headers={"Authorization": f"Bearer {settings.image_gen_api_key}"},
            data={
                "model": settings.image_model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
            },
            files=files,
            timeout=300,
        )

    return _image_post_retry(do_post)


def _generate_gemini(prompt: str) -> bytes:
    resp = httpx.post(
        GEMINI_IMAGES_URL,
        params={"key": settings.image_gen_api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        },
        timeout=180,
    )
    resp.raise_for_status()
    for part in resp.json()["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("Gemini did not return an image")


def _generate(prompt: str) -> bytes:
    if settings.image_gen_provider == "gemini":
        return _generate_gemini(prompt)
    return _generate_openai(prompt)


def pick_random_avatar(session: Session) -> str | None:
    avatar = session.scalar(select(AvatarPool).order_by(func.random()).limit(1))
    return avatar.image_url if avatar else None


def _random_avatar_bytes(session: Session) -> bytes | None:
    """Байты случайной аватарки из пула (локальный файл)."""
    url = pick_random_avatar(session)
    if not url:
        return None
    path = Path(settings.media_dir) / url.rsplit("/", 1)[-1]
    return path.read_bytes() if path.exists() else None


def pick_avatar_name(session: Session) -> str | None:
    """Имя файла случайной аватарки из пула — чтобы «закрепить» одного ученика
    за постом (тот же аватар при смене текста/продолжении в template-режиме)."""
    url = pick_random_avatar(session)
    return url.rsplit("/", 1)[-1] if url else None


def _avatar_bytes_by_name(name: str | None) -> bytes | None:
    if not name:
        return None
    path = Path(settings.media_dir) / name
    return path.read_bytes() if path.exists() else None


def _render_template(
    session: Session,
    app: str,
    sender: str,
    message: str,
    amount: str | None,
    avatar_name: str | None = None,
) -> bytes:
    from app.images.notification import render_notification

    avatar_bytes = _avatar_bytes_by_name(avatar_name) or _random_avatar_bytes(session)
    if avatar_bytes is None:
        buf = io.BytesIO()
        Image.new("RGB", (130, 130), (90, 90, 96)).save(buf, format="PNG")
        avatar_bytes = buf.getvalue()
    return render_notification(sender, message, avatar_bytes, app=app, amount=amount)


# разнообразие для СВЕЖИХ скринов, чтобы посты не выглядели одинаково
_VARIETY_WALLPAPERS = [
    "a dark moody gradient", "a soft pastel blur", "a blurred forest/nature photo",
    "a city night bokeh blur", "a plain dark grey wallpaper", "a warm sunset blur",
    "a blue-purple gradient", "an abstract colorful blur", "a blurred cozy room",
    "a minimal beige wallpaper",
]
# аватары по полу — чтобы пол на фото совпадал с полом имени отправителя
_MALE_LOOKS = [
    "a man around 30", "a young man ~25", "a teenage boy",
    "a man ~35 with a beard", "a young man ~25 with glasses",
    "a young guy in a hoodie", "a middle-aged man", "a serious-looking male student",
]
_FEMALE_LOOKS = [
    "a young woman around 20", "a woman around 40", "a girl ~18",
    "a woman ~28", "a smiling young woman", "a young woman ~25 with glasses",
    "a female student ~22",
]

# мужские имена/уменьшительные, оканчивающиеся на гласную (иначе правило «-а/-я = ж»)
_MALE_VOWEL_NAMES = {
    "діма", "дима", "міша", "миша", "гриша", "льоша", "лёоша", "лёша", "альоша",
    "паша", "гоша", "жора", "ілля", "илья", "микита", "никита", "кузьма", "фома",
    "лука", "хома", "сава", "сєня", "сеня", "вітя", "витя", "костя", "петя",
    "вася", "толя", "коля", "юра", "гена", "стьопа", "стёпа", "боря", "сашко",
}
_FEMALE_NAMES = {
    "оля", "аня", "катя", "настя", "маша", "даша", "юля", "ліза", "лиза", "віка",
    "вика", "поля", "галя", "іра", "ира", "таня", "софія", "марія", "олена",
    "ірина", "наталя", "світлана", "юлія", "анна", "оксана", "карина", "аліна",
    "поліна", "злата", "мілана", "христина", "діана", "вероніка", "яна", "інна",
}


def _name_gender(sender: str) -> str:
    """«man» / «woman» по имени отправителя (для совпадения аватара с именем)."""
    s = (sender or "").lower()
    if "учениця" in s or "ученица" in s:
        return "woman"
    tokens = [
        t for t in s.replace("-", " ").split()
        if t not in ("учень", "ученик", "учениця", "ученица", "студент", "студентка")
    ]
    name = tokens[0] if tokens else s.strip()
    if name in _FEMALE_NAMES:
        return "woman"
    if name in _MALE_VOWEL_NAMES:
        return "man"
    if name.endswith(("а", "я")):
        return "woman"
    return "man"


def _generate_ai_notification(
    session: Session,
    app: str,
    sender: str,
    message: str,
    amount: str | None,
    base_image_bytes: bytes | None = None,
) -> bytes:
    """Генерация скриншота нейросетью по референсам клиента (gpt-image-2).

    base_image_bytes задан -> «тот же ученик»: берём предыдущий скрин главным
    референсом и меняем ТОЛЬКО текст (для смены цены/сообщения, продолжения).
    base_image_bytes=None -> свежий скрин: подмешиваем случайные обои/аватар,
    чтобы разные посты не были на одно лицо."""
    common = (
        "CRITICAL — the result MUST look like a REAL phone screenshot, NOT an AI "
        "picture. Reproduce the reference screenshot's UI EXACTLY 1:1: identical "
        "bubble shape and corner radius, identical semi-transparent dark bubble, the "
        "SAME font family, size, weight and line spacing, the same paddings and the "
        "same overall layout as in the reference. Change ONLY the text content and "
        "the avatar. It is a phone lock-screen message notification over a blurred "
        "wallpaper; the wallpaper fills the ENTIRE frame edge to edge (no white "
        "margins, no borders). Text is PERFECTLY legible, EXACT wording, natural "
        "Ukrainian with correct letters (і, ї, є, ґ). Time label \"сейчас\" at the "
        "top-right of the bubble. ABSOLUTELY NO watermarks, no warped or gibberish "
        "letters, no extra UI, no duplicated bubbles, no AI artifacts. A soft, "
        "realistic drop shadow beneath the notification bubble, like a real iOS "
        "lock-screen. Subtle, believable phone-screen realism."
    )
    keep = base_image_bytes is not None
    if keep:
        common += (
            " CRITICAL: keep the EXACT same avatar/person face and the EXACT same "
            "wallpaper background as the FIRST reference image — change ONLY the "
            "notification text to the new wording."
        )
    is_mono = app.lower() == "monobank" and amount
    if is_mono:
        prompt = (
            f"Recreate the reference monobank push notification 1:1 — same layout, "
            f"same dark bubble, same background style. Change ONLY the text. The app "
            f"icon on the left is ONLY the black rounded 'mono' square — there is NO "
            f"person avatar at all. Top line: a pointing-finger emoji, a small bank "
            f"card, then the amount \"{amount}\" in bold. Next line \"Від: {sender}\". "
            f"Next line \"Баланс:\" (WITH a colon) followed by the number scribbled "
            f"out. Then \"Коментар: {message}\" (the word Коментар MUST be followed "
            f"by a colon). " + common
        )
        style_refs = _load_style_refs(session, limit=2, kind="monobank")
    else:
        # аватар СОВПАДАЕТ по полу с именем отправителя
        gender = _name_gender(sender)
        looks = _MALE_LOOKS if gender == "man" else _FEMALE_LOOKS
        if not keep:
            variety = (
                f" Avatar: a photo of {random.choice(looks)} — the avatar MUST be "
                f"{'MALE' if gender == 'man' else 'FEMALE'} to match the name "
                f"\"{sender}\" (generic fictional person, not real or famous). "
                f"Wallpaper: {random.choice(_VARIETY_WALLPAPERS)}."
            )
        else:
            variety = (
                f" Avatar of a generic fictional {'man' if gender == 'man' else 'woman'} "
                f"(not real or famous), matching the name \"{sender}\"."
            )
        # по умолчанию ОДНО сообщение; 2-3 пузыря — только для очень длинного текста
        multi = (not keep) and len(message) > 200
        bubble_instr = (
            " Render the message as 2-3 SEPARATE stacked chat bubbles from the SAME "
            "sender (as if they sent several messages in a row), each bubble in the "
            "exact same style; split the text naturally between the bubbles."
            if multi else " A SINGLE message bubble (one bubble only)."
        )
        prompt = (
            f"An incoming Telegram message notification. Contact name \"{sender}\" at "
            f"the top, small Telegram logo near the avatar. Message text: "
            f"\"{message}\".{bubble_instr}{variety} " + common
        )
        style_refs = _load_style_refs(session, limit=2, kind="telegram")
    refs = ([base_image_bytes] + style_refs[:1]) if keep else style_refs
    if refs:
        return _generate_openai_with_refs(prompt, refs)
    return _generate_openai(prompt)


def _crop_to_bubble(image_bytes: bytes, margin_frac: float = 0.04) -> bytes:
    """Обрезает картинку почти под сам баббл (тёмный прямоугольник сообщения),
    чтобы выглядело как реальный обрезанный скрин, а не сообщение на обоях.

    Вертикаль — по строкам, где тёмное покрывает большую часть ширины (надёжно).
    Горизонталь — по крайним тёмным пикселям ВНУТРИ этой полосы (не режет баббл)."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        W, H = img.size
        sw = 320
        sh = max(1, int(sw * H / W))
        px = img.convert("L").resize((sw, sh)).load()
        vals = sorted(px[x, y] for y in range(0, sh, 2) for x in range(0, sw, 2))
        med = vals[len(vals) // 2]
        thr = min(95, med - 15)  # баббл темнее фона

        # баббл покрывает ЦЕНТР кадра; тёмные углы обоев — нет.
        cx0, cx1 = int(sw * 0.38), int(sw * 0.62)
        cw = cx1 - cx0

        def bubble_row(y):
            return sum(px[x, y] < thr for x in range(cx0, cx1)) / cw > 0.75

        rows = [y for y in range(sh) if bubble_row(y)]
        if not rows:
            return image_bytes
        runs, s, p = [], rows[0], rows[0]
        for i in rows[1:]:
            if i == p + 1:
                p = i
            else:
                runs.append((s, p)); s = i; p = i
        runs.append((s, p))
        y0, y1 = max(runs, key=lambda r: r[1] - r[0])
        if not (0.08 < (y1 - y0) / sh < 0.9):
            return image_bytes  # нет чёткого баббла — не трогаем

        # горизонталь: колонки, тёмные на бОльшей части полосы = тело баббла
        band = list(range(y0, y1 + 1))
        cols = [x for x in range(sw) if sum(px[x, y] < thr for y in band) / len(band) > 0.5]
        if not cols:
            return image_bytes
        x0, x1 = min(cols), max(cols)

        sx, sy = W / sw, H / sh
        mv, mh = int(margin_frac * H), int(margin_frac * W)
        box = (
            max(0, int(x0 * sx) - mh), max(0, int(y0 * sy) - mv),
            min(W, int((x1 + 1) * sx) + mh), min(H, int((y1 + 1) * sy) + mv),
        )
        if box[2] - box[0] < W * 0.4 or box[3] - box[1] < H * 0.08:
            return image_bytes
        out = io.BytesIO()
        img.crop(box).save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return image_bytes


def generate_notification_image(
    session: Session,
    app: str,
    sender: str,
    message: str,
    amount: str | None = None,
    base_image_bytes: bytes | None = None,
    avatar_name: str | None = None,
) -> str:
    """Скриншот-уведомление для POV. Режим — settings.notification_render_mode:
    'ai' (генерация gpt-image-2 по референсам клиента) или 'template' (Pillow).

    «Тот же ученик» при смене текста/продолжении: в ai-режиме — base_image_bytes,
    в template-режиме — avatar_name (закреплённый аватар из пула)."""
    if settings.notification_render_mode == "template":
        image_bytes = _render_template(session, app, sender, message, amount, avatar_name)
    else:
        try:
            image_bytes = _generate_ai_notification(
                session, app, sender, message, amount, base_image_bytes
            )
        except Exception:
            image_bytes = _render_template(
                session, app, sender, message, amount, avatar_name
            )  # фолбэк
    return _save_image(image_bytes, "notif")


def generate_avatar_pool(session: Session, count: int = 18) -> list[str]:
    """Разовая генерация пула синтетических аватаров (ТЗ п.8).

    Важно: не использовать лица знаменитостей/актёров — промпт генерирует
    вымышленных людей.
    """
    urls = []
    for i in range(count):
        prompt = (
            "A casual smartphone-style profile photo (avatar) of a completely "
            "fictional, synthetic person who does not resemble any real or famous "
            "person. Ordinary everyday look, neutral background, head and "
            f"shoulders, natural lighting. Variation #{i + 1}: vary age (20-45), "
            "gender, hairstyle and clothing."
        )
        image_bytes = _generate(prompt)
        url = _save_image(image_bytes, "avatar")
        session.add(AvatarPool(image_url=url))
        urls.append(url)
    session.flush()
    return urls
