"""Программная сборка скриншота-уведомления (ТЗ: формат «1 в 1» с реальными постами).

Детерминированный рендер шаблона Pillow под ТОЧНЫЙ формат клиента: широкий
баннер ~3:1 (как его реальные скриншоты 1752x592, 1931x528), реальные шрифты,
логотипы Telegram/monobank, реалистичный размытый фон-обои. Меняются только
текст, ник, сумма и аватарка. Поддерживает Telegram-сообщение и monobank-перевод.
"""
import io
import random

import emoji as emoji_lib
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from pilmoji import Pilmoji

    _HAS_PILMOJI = True
except Exception:  # pragma: no cover
    _HAS_PILMOJI = False

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

W = 1600
# тайтовые поля: обрезано почти под сам баббл (как реальный скрин)
MARGIN_H = 26
MARGIN_V = 30
PAD = 46
ICON = 132              # аватар/иконка приложения
GAP = 30
NAME_SIZE = 52
MSG_SIZE = 48
TIME_SIZE = 40
LINE_H = 64
RADIUS = 52

BUBBLE = (28, 28, 31, 232)
NAME_COL = (255, 255, 255)
MSG_COL = (238, 238, 242)
TIME_COL = (150, 150, 156)
TG_BLUE = (42, 171, 238)

# насыщенные размытые «обои» — как реальные фоны клиента
PALETTES = [
    [(126, 92, 200), (230, 200, 90), (90, 160, 180)],
    [(150, 90, 180), (90, 180, 140), (210, 170, 90)],
    [(70, 100, 170), (200, 120, 160), (120, 190, 170)],
    [(40, 44, 56), (90, 70, 110), (60, 90, 100)],
    [(180, 120, 90), (110, 90, 150), (200, 180, 120)],
]


def _font(path, size):
    return ImageFont.truetype(path, size)


def _text_w(draw, text, font):
    stripped = emoji_lib.replace_emoji(text, "")
    n = emoji_lib.emoji_count(text)
    return draw.textlength(stripped, font=font) + n * (font.size + 6)


def _wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip()
            if not cur or _text_w(draw, trial, font) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
    return lines


def _circle_avatar(avatar_bytes, d):
    av = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((d, d))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d, d), fill=255)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(av, (0, 0), mask)
    return out


def _tg_badge(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=TG_BLUE)
    s = size
    plane = [
        (s * 0.20, s * 0.52), (s * 0.80, s * 0.26), (s * 0.66, s * 0.76),
        (s * 0.52, s * 0.60), (s * 0.44, s * 0.70), (s * 0.44, s * 0.54),
    ]
    d.polygon(plane, fill=(255, 255, 255))
    d.line([(s * 0.44, s * 0.54), (s * 0.66, s * 0.34)], fill=TG_BLUE, width=max(2, size // 36))
    return img


def _mono_icon(size):
    """Чёрная скруглённая иконка monobank с текстом 'mono'."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size, size), radius=int(size * 0.24), fill=(18, 18, 18))
    f = _font(FONT_BOLD, int(size * 0.28))
    t = "mono"
    tw = d.textlength(t, font=f)
    d.text(((size - tw) / 2, size * 0.36), t, font=f, fill=(255, 255, 255))
    return img


def _background(w, h):
    pal = random.choice(PALETTES)
    base = Image.new("RGB", (w, h), pal[-1])
    d = ImageDraw.Draw(base)
    for _ in range(5):
        col = random.choice(pal)
        cx, cy = random.randint(0, w), random.randint(0, h)
        rw, rh = random.randint(w // 3, w // 2), random.randint(h // 2, h)
        d.ellipse((cx - rw, cy - rh, cx + rw, cy + rh), fill=col)
    base = base.filter(ImageFilter.GaussianBlur(160))
    # лёгкое затемнение, чтобы белый текст читався
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.blend(base, dark, 0.18)


def _draw_line(base, pos, text, font, fill):
    if _HAS_PILMOJI:
        try:
            with Pilmoji(base) as p:
                p.text(pos, text, fill=fill, font=font, emoji_scale_factor=0.92)
            return
        except Exception:
            pass
    ImageDraw.Draw(base).text(pos, emoji_lib.replace_emoji(text, ""), font=font, fill=fill)


def render_notification(sender, message, avatar_bytes, app="telegram", amount=None):
    name_font = _font(FONT_BOLD, NAME_SIZE)
    msg_font = _font(FONT_REG, MSG_SIZE)
    time_font = _font(FONT_REG, TIME_SIZE)

    is_mono = app.lower() == "monobank" and amount
    bubble_w = W - 2 * MARGIN_H
    text_x = MARGIN_H + PAD + ICON + GAP
    text_w = MARGIN_H + bubble_w - PAD - text_x

    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    # формируем строки текста
    if is_mono:
        head = f"👉 💳 {amount}"
        rows = [(name_font, head), (msg_font, f"Від: {sender}"),
                (msg_font, "Баланс:"), (msg_font, f"Коментар: {message}")]
        # разворачиваем последнюю (коммент) с переносом
        flat = []
        for f, t in rows[:-1]:
            flat.append((f, t))
        for ln in _wrap(tmp, rows[-1][1], msg_font, text_w):
            flat.append((msg_font, ln))
        lines = flat
    else:
        lines = [(name_font, sender)] + [(msg_font, ln) for ln in _wrap(tmp, message, msg_font, text_w)]

    content_h = sum(LINE_H for _ in lines)
    inner_h = max(content_h, ICON)
    bubble_h = inner_h + 2 * PAD
    H = bubble_h + 2 * MARGIN_V

    base = _background(W, H).convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        (MARGIN_H, MARGIN_V, MARGIN_H + bubble_w, MARGIN_V + bubble_h), radius=RADIUS, fill=BUBBLE
    )
    base = Image.alpha_composite(base, overlay)

    ax, ay = MARGIN_H + PAD, MARGIN_V + PAD
    if is_mono:
        icon = _mono_icon(ICON)
        base.paste(icon, (ax, ay), icon)
    else:
        av = _circle_avatar(avatar_bytes, ICON)
        base.paste(av, (ax, ay), av)
        badge = _tg_badge(52)
        base.paste(badge, (ax + ICON - 44, ay + ICON - 44), badge)

    d = ImageDraw.Draw(base)
    tw = d.textlength("зараз", font=time_font)
    d.text((MARGIN_H + bubble_w - PAD - tw, ay + 6), "зараз", font=time_font, fill=TIME_COL)

    y = MARGIN_V + PAD
    for i, (font, text) in enumerate(lines):
        col = NAME_COL if (i == 0) else MSG_COL
        _draw_line(base, (text_x, y), text, font, col)
        # красная замазка баланса (monobank)
        if is_mono and text == "Баланс:":
            bx = text_x + d.textlength("Баланс: ", font=msg_font)
            d.line([(bx, y + LINE_H * 0.5), (bx + 320, y + LINE_H * 0.5)],
                   fill=(230, 30, 30), width=26)
        y += LINE_H

    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
