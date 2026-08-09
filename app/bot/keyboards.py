from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_STATS = "📊 Статистика"
BTN_DRAFTS = "📝 Чернетки постів"
BTN_OWN_POST = "➕ Свій пост"
BTN_HELP = "📖 Інструкція"
BTN_SERIES = "🔁 Продовжити пост"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_DRAFTS)],
            [KeyboardButton(text=BTN_OWN_POST), KeyboardButton(text=BTN_SERIES)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def sequel_list_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Список постов-кандидатов на продолжение серии."""
    rows = [
        [InlineKeyboardButton(text=label[:60], callback_data=f"series:{pid}")]
        for pid, label in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stats_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="7 днів", callback_data="stats:7"),
                InlineKeyboardButton(text="30 днів", callback_data="stats:30"),
            ]
        ]
    )


def trend_topics_kb(topics: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=headline[:60], callback_data=f"gen:trend:{topic_id}")]
        for topic_id, headline in topics
    ]
    rows.append(
        [InlineKeyboardButton(text="✍️ Своя тема", callback_data="gen:custom")]
    )
    rows.append(
        [InlineKeyboardButton(text="🔎 Оновити теми", callback_data="trends:refresh")]
    )
    rows.append(
        [InlineKeyboardButton(text="Без трендової теми", callback_data="gen:notrend")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rating_kb(post_id: int) -> InlineKeyboardMarkup:
    """Кнопки разметки голоса в режиме обучения (/train)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Його голос", callback_data=f"rate:good:{post_id}"),
                InlineKeyboardButton(text="❌ Не його", callback_data=f"rate:bad:{post_id}"),
            ]
        ]
    )


def img_rating_kb(post_id: int) -> InlineKeyboardMarkup:
    """Оценка стиля картинки: одобренная идёт в пул референсов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Стиль ок", callback_data=f"imgrate:good:{post_id}"),
                InlineKeyboardButton(text="❌ Не той стиль", callback_data=f"imgrate:bad:{post_id}"),
            ]
        ]
    )


def meme_captions_kb(post_id: int, n: int) -> InlineKeyboardMarkup:
    """Выбор одного из вариантов подписи к загруженному мему."""
    rows = [
        [InlineKeyboardButton(text=f"Варіант {i + 1}", callback_data=f"memecap:{post_id}:{i}")]
        for i in range(n)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lead_kb(post_id: int, brought: bool) -> InlineKeyboardMarkup:
    mark = "✅ Приніс запит" if brought else "Позначити: приніс запит"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=mark, callback_data=f"lead:{post_id}")]]
    )


def dataset_more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="▶️ Ще 8", callback_data="dataset:more")]]
    )


def draft_kb(post_id: int, with_image: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✅ Опублікувати", callback_data=f"draft:approve:{post_id}"),
            InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"draft:edit:{post_id}"),
        ],
        [InlineKeyboardButton(text="❌ Відхилити", callback_data=f"draft:reject:{post_id}")],
    ]
    if with_image:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Перегенерувати фото", callback_data=f"draft:regen:{post_id}"
                ),
                InlineKeyboardButton(
                    text="✏️ Змінити текст на фото", callback_data=f"draft:edittext:{post_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔁 Продовження (той самий учень)",
                    callback_data=f"draft:sequel:{post_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
