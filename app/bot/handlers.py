import asyncio
import html
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.bot import keyboards as kb
from app.bot import service
from app.config import settings
from app.models import PostStatus
from app.tasks.publish import publish_post


def _local_photo(image_url: str) -> FSInputFile | str:
    """Локальный файл по URL картинки (надёжнее URL — Telegram не качает наш сервер).
    Фолбэк на URL, если файла нет."""
    fname = image_url.rsplit("/", 1)[-1]
    path = Path(settings.media_dir) / fname
    return FSInputFile(path) if path.exists() else image_url

router = Router()

ARCHETYPE_TITLES = {
    "pov_confession": "POV / зізнання",
    "educational": "Навчальний",
    "trend_reaction": "Реакція на тренд",
    "meme_notification": "Мем",
    "transformation": "Трансформація 🎯",
    "pain_point": "Біль→рішення 🎯",
    "lead_magnet": "Лід-магніт 🎯",
}


class EditDraft(StatesGroup):
    waiting_text = State()


class OwnPost(StatesGroup):
    waiting_text = State()


class CustomTopic(StatesGroup):
    waiting_topic = State()


class EditImageText(StatesGroup):
    waiting_text = State()


# --- /start и меню -----------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привіт! Я бот автоматизації Threads.\n\n"
        "📊 Статистика — перегляди, взаємодії, топ-пости\n"
        "📝 Чернетки постів — згенерувати чернетки на апрув\n"
        "➕ Свій пост — надіслати власний текст у чергу публікації\n"
        "😂 Мем — просто надішли фото, я придумаю 3 варіанти підпису\n"
        "📖 Інструкція — детальна методичка, як усім користуватися\n\n"
        "🎓 /dataset — навчання голосу: оцінюй пости ✅/❌\n"
        "🎯 /leads — познач пости, що привели запит (аналітика конверсії)",
        reply_markup=kb.main_menu(),
    )


@router.message(F.text == kb.BTN_HELP)
@router.message(Command("help"))
async def cmd_help(message: Message):
    from app.bot.guide import GUIDE_PARTS

    for part in GUIDE_PARTS:
        await message.answer(part)


DATASET_BATCH = 8


async def _send_dataset_batch(message: Message):
    """Генерит пачку постов для разметки голоса (POV со скриншотом, остальное текст)."""
    try:
        drafts = await asyncio.to_thread(
            service.generate_drafts_and_save, None, None, DATASET_BATCH
        )
    except Exception as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return

    text_drafts = [d for d in drafts if not d["has_image"]]
    image_drafts = [d for d in drafts if d["has_image"]]

    for d in text_drafts:
        title = ARCHETYPE_TITLES.get(d["archetype"], d["archetype"])
        await message.answer(
            f"<b>[{title}]</b>\n\n{html.escape(d['text'])}",
            reply_markup=kb.rating_kb(d["post_id"]),
        )
    if image_drafts:
        urls = await asyncio.gather(
            *[asyncio.to_thread(service.regenerate_post_image, d["post_id"]) for d in image_drafts],
            return_exceptions=True,
        )
        for d, url in zip(image_drafts, urls):
            title = ARCHETYPE_TITLES.get(d["archetype"], d["archetype"])
            caption = f"<b>[{title}]</b>\n\n{html.escape(d['text'])}"
            if isinstance(url, Exception) or not url:
                await message.answer(caption, reply_markup=kb.rating_kb(d["post_id"]))
            else:
                await message.answer_photo(
                    _local_photo(url), caption=caption[:1024], reply_markup=kb.rating_kb(d["post_id"])
                )

    good, bad = await asyncio.to_thread(service.rating_counts)
    await message.answer(
        f"Оцінено: ✅ {good} / ❌ {bad}. Постав оцінку кожному вище й тисни «Ще» "
        f"для наступної пачки (ціль ~100 для датасету).",
        reply_markup=kb.dataset_more_kb(),
    )


@router.message(Command("train"))
@router.message(Command("dataset"))
async def cmd_dataset(message: Message):
    await message.answer(
        "🎓 Датасет для навчання голосу.\nГенерую пачку постів — постав кожному "
        "✅ (його голос) або ❌ (не його). Вдалі стають еталоном, невдалі — "
        "прикладом «як не треба». Тисни «Ще» скільки треба."
    )
    await _send_dataset_batch(message)


@router.callback_query(F.data == "dataset:more")
async def dataset_more(callback: CallbackQuery):
    await callback.answer("Генерую ще…")
    await _send_dataset_batch(callback.message)


@router.message(Command("leads"))
async def cmd_leads(message: Message):
    posts = await asyncio.to_thread(service.recent_published, 10)
    if not posts:
        await message.answer("Ще немає опублікованих постів.")
        return
    await message.answer(
        "🎯 Познач пости, які принесли запит/ученика в директ — це навчить систему, "
        "які формати реально конвертять:"
    )
    for p in posts:
        await message.answer(
            f"#{p['post_id']}: {html.escape(p['text'])}…",
            reply_markup=kb.lead_kb(p["post_id"], p["brought_lead"]),
        )


@router.callback_query(F.data.startswith("lead:"))
async def lead_callback(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    val = await asyncio.to_thread(service.toggle_lead, post_id)
    if val is None:
        await callback.answer("Пост не знайдено", show_alert=True)
        return
    await callback.answer("Позначено ✅" if val else "Знято")
    await callback.message.edit_reply_markup(reply_markup=kb.lead_kb(post_id, val))


@router.callback_query(F.data.startswith("rate:"))
async def rate_callback(callback: CallbackQuery):
    _, rating, post_id = callback.data.split(":")
    ok = await asyncio.to_thread(service.set_voice_rating, int(post_id), rating)
    if not ok:
        await callback.answer("Пост не знайдено", show_alert=True)
        return
    await callback.answer("✅ Зараховано" if rating == "good" else "❌ Зараховано")
    mark = "✅ Його голос" if rating == "good" else "❌ Не його"
    base = callback.message.html_text or callback.message.text or ""
    await callback.message.edit_text(f"{base}\n\n<i>Оцінка: {mark}</i>")


@router.callback_query(F.data.startswith("imgrate:"))
async def imgrate_callback(callback: CallbackQuery):
    _, rating, post_id = callback.data.split(":")
    if rating == "good":
        ok = await asyncio.to_thread(service.approve_image_reference, int(post_id))
        await callback.answer("✅ Додано в референси стилю" if ok else "Не вдалося", show_alert=not ok)
        mark = "✅ Стиль ок (у референси)"
    else:
        await asyncio.to_thread(service.set_voice_rating, int(post_id), "bad")
        await callback.answer("❌ Зараховано")
        mark = "❌ Не той стиль"
    base = callback.message.html_text or callback.message.caption or ""
    await callback.message.edit_caption(caption=f"{base}\n\n<i>{mark}</i>"[:1024])


# --- Статистика --------------------------------------------------------------

@router.message(F.text == kb.BTN_STATS)
async def stats_menu(message: Message):
    await message.answer("За який період?", reply_markup=kb.stats_period_kb())


@router.callback_query(F.data.startswith("stats:"))
async def stats_show(callback: CallbackQuery):
    days = int(callback.data.split(":")[1])
    await callback.answer()
    text = await asyncio.to_thread(service.build_stats, days)
    await callback.message.answer(text)


# --- Генерация черновиков ----------------------------------------------------

@router.message(F.text == kb.BTN_DRAFTS)
@router.message(Command("generate"))
async def drafts_menu(message: Message):
    topics = await asyncio.to_thread(service.list_unused_trends)
    if topics:
        text = (
            "Є актуальні теми під архетип «реакція на тренд». "
            "Обери тему, введи свою або генеруй без теми:"
        )
    else:
        text = (
            "Актуальних тем немає. Можеш оновити теми, ввести свою "
            "або генерувати без теми:"
        )
    await message.answer(text, reply_markup=kb.trend_topics_kb(topics))


@router.callback_query(F.data == "trends:refresh")
async def trends_refresh(callback: CallbackQuery):
    await callback.answer("Шукаю теми…")
    await callback.message.answer("🔎 Шукаю актуальні теми, це ~20-40 секунд…")
    try:
        topics = await asyncio.to_thread(service.refresh_trends)
    except Exception as exc:
        await callback.message.answer(f"⚠️ Помилка пошуку тем: {html.escape(str(exc))}")
        return
    if topics:
        await callback.message.answer(
            "Знайшов теми. Обери або введи свою:",
            reply_markup=kb.trend_topics_kb(topics),
        )
    else:
        await callback.message.answer(
            "Підходящих тем зараз не знайшлось (фільтр суворий). "
            "Введи свою тему або генеруй без теми:",
            reply_markup=kb.trend_topics_kb([]),
        )


@router.callback_query(F.data == "gen:custom")
async def gen_custom_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CustomTopic.waiting_topic)
    await callback.answer()
    await callback.message.answer(
        "Напиши свою тему/інфопривід наступним повідомленням "
        "(напр.: «своя актуальна тема поста»)."
    )


@router.message(CustomTopic.waiting_topic, F.text)
async def gen_custom_topic(message: Message, state: FSMContext):
    await state.clear()
    topic = message.text.strip()
    await message.answer(f"Генерую чернетки на тему «{html.escape(topic)}»…")
    await _generate_and_send(message, custom_headline=topic)


@router.callback_query(F.data.startswith("gen:"))
async def gen_callback(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    trend_topic_id = int(parts[2]) if parts[1] == "trend" else None
    await callback.message.answer("Генерую чернетки, це ~30-60 секунд…")
    await _generate_and_send(callback.message, trend_topic_id=trend_topic_id)


async def _generate_and_send(
    message: Message,
    trend_topic_id: int | None = None,
    custom_headline: str | None = None,
):
    try:
        drafts = await asyncio.to_thread(
            service.generate_drafts_and_save, trend_topic_id, custom_headline
        )
    except Exception as exc:
        await message.answer(f"⚠️ Помилка генерації: {html.escape(str(exc))}")
        return

    # текстовые карточки шлём сразу — пользователь не ждёт генерации картинок
    text_drafts = [d for d in drafts if not d["has_image"]]
    image_drafts = [d for d in drafts if d["has_image"]]
    for d in text_drafts:
        await _send_text_card(message, d)

    if not image_drafts:
        return

    # картинки генерим параллельно и досылаем по готовности
    await message.answer(f"🖼 Генерую фото для {len(image_drafts)} пост(ів)…")
    urls = await asyncio.gather(
        *[asyncio.to_thread(service.regenerate_post_image, d["post_id"]) for d in image_drafts],
        return_exceptions=True,
    )
    for d, url in zip(image_drafts, urls):
        if isinstance(url, Exception) or not url:
            # фото не вышло — шлём текстом с кнопкой ручной перегенерации
            await _send_text_card(message, d, with_image_button=True)
        else:
            await _send_photo_card(message, d, url)


def _caption(d: dict) -> str:
    title = ARCHETYPE_TITLES.get(d["archetype"], d["archetype"])
    return f"<b>[{title}]</b>\n\n{html.escape(d['text'])}"


async def _send_first_comment(message: Message, d: dict):
    """Отдельным сообщением — первый комментарий (перевод/ответ), который клиент
    добавляет под постом и закрепляет. Присылаем отдельно, чтобы удобно копировать."""
    fc = d.get("first_comment")
    if not fc:
        return
    await message.answer(
        "💬 <b>Перший коментар</b> (додай під постом і 📌 закріпи):\n\n"
        f"{html.escape(fc)}"
    )


async def _send_text_card(message: Message, d: dict, with_image_button: bool = False):
    await message.answer(
        _caption(d), reply_markup=kb.draft_kb(d["post_id"], with_image=with_image_button)
    )
    await _send_first_comment(message, d)


async def _send_photo_card(message: Message, d: dict, url: str):
    await message.answer_photo(
        _local_photo(url),
        caption=_caption(d)[:1024],
        reply_markup=kb.draft_kb(d["post_id"], with_image=True),
    )
    await _send_first_comment(message, d)


# --- Кнопки карточки черновика -----------------------------------------------

@router.callback_query(F.data.startswith("draft:approve:"))
async def draft_approve(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[2])
    ok = await asyncio.to_thread(service.set_post_status, post_id, PostStatus.approved)
    if not ok:
        await callback.answer("Пост не знайдено", show_alert=True)
        return
    publish_post.delay(post_id)  # SLA: публикация не позднее 15 мин после апрува
    await callback.answer("У черзі на публікацію ✅")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Пост #{post_id} поставлено в чергу на публікацію.")


@router.callback_query(F.data.startswith("draft:reject:"))
async def draft_reject(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[2])
    await asyncio.to_thread(service.set_post_status, post_id, PostStatus.rejected)
    await callback.answer("Відхилено ❌")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("draft:edit:"))
async def draft_edit(callback: CallbackQuery, state: FSMContext):
    post_id = int(callback.data.split(":")[2])
    await state.set_state(EditDraft.waiting_text)
    await state.update_data(post_id=post_id)
    await callback.answer()
    await callback.message.answer(
        f"Надішли новий текст для поста #{post_id} наступним повідомленням."
    )


@router.message(EditDraft.waiting_text, F.text)
async def draft_edit_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Надішли новий текст повідомленням.")
        return
    data = await state.get_data()
    post_id = data["post_id"]
    await state.clear()
    ok = await asyncio.to_thread(service.update_post_text, post_id, message.text)
    if not ok:
        await message.answer("Пост не знайдено.")
        return
    await message.answer(
        f"Текст поста #{post_id} оновлено. Опублікувати?",
        reply_markup=kb.draft_kb(post_id, with_image=False),
    )


@router.callback_query(F.data.startswith("draft:regen:"))
async def draft_regen_photo(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[2])
    await callback.answer("Генерую нове фото…")
    url = await asyncio.to_thread(service.regenerate_post_image, post_id)
    if url is None:
        await callback.message.answer("Для цього поста немає даних сповіщення.")
        return
    await callback.message.answer_photo(
        _local_photo(url),
        caption=f"Нове фото для поста #{post_id}",
        reply_markup=kb.draft_kb(post_id, with_image=True),
    )


async def _make_sequel(message: Message, post_id: int):
    await message.answer("Роблю продовження з тим самим учнем…")
    try:
        res = await asyncio.to_thread(service.create_continuation_draft, post_id)
    except Exception as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    if res is None:
        await message.answer("Продовження можливе лише для POV зі скріншотом.")
        return
    d = {"post_id": res["post_id"], "archetype": res["archetype"], "text": res["text"]}
    await _send_photo_card(message, d, res["url"])


@router.callback_query(F.data.startswith("draft:sequel:"))
async def draft_sequel(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[2])
    await callback.answer()
    await _make_sequel(callback.message, post_id)


@router.message(F.text == kb.BTN_SERIES)
@router.message(Command("series"))
async def series_start(message: Message):
    items = await asyncio.to_thread(service.list_sequelable_posts)
    if not items:
        await message.answer(
            "Поки немає постів зі скріншотом для продовження. Спершу згенеруй "
            "POV-пост (📝 Чернетки постів)."
        )
        return
    await message.answer(
        "Обери пост, який продовжити (той самий учень, наступна серія):",
        reply_markup=kb.sequel_list_kb(items),
    )


@router.callback_query(F.data.startswith("series:"))
async def series_pick(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _make_sequel(callback.message, post_id)


@router.callback_query(F.data.startswith("draft:edittext:"))
async def draft_edit_image_text(callback: CallbackQuery, state: FSMContext):
    post_id = int(callback.data.split(":")[2])
    await state.set_state(EditImageText.waiting_text)
    await state.update_data(post_id=post_id)
    await callback.answer()
    await callback.message.answer(
        "Напиши, що змінити на фото (можна коряво — я приведу до ладу).\n"
        "Напр.: «повідомлення: не прийду, захворів» або «коментар: дякую за урок, "
        "решту завтра» чи просто новий текст."
    )


@router.message(EditImageText.waiting_text, F.text)
async def draft_edit_image_text_apply(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Напиши текстом, що змінити.")
        return
    data = await state.get_data()
    post_id = data["post_id"]
    await state.clear()
    await message.answer("Перегенеровую фото з новим текстом…")
    try:
        res = await asyncio.to_thread(service.change_image_text, post_id, message.text)
    except Exception as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    if res is None:
        await message.answer("Для цього поста немає даних сповіщення.")
        return
    await message.answer_photo(
        _local_photo(res["url"]),
        caption=f"Оновлене фото для поста #{post_id}:\n\n{html.escape(res['message'])}",
        reply_markup=kb.draft_kb(post_id, with_image=True),
    )


# --- Мем-формат: фото от учителя -> 3 варианта подписи -----------------------

@router.message(F.photo)
async def meme_photo(message: Message, bot: Bot, state: FSMContext):
    # фото = однозначно мем-загрузка: сбрасываем любое ожидание текста,
    # чтобы фото не «проглатывалось» состоянием (Свій пост / зміна тексту)
    await state.clear()
    await message.answer("😂 Придумую підписи до мема, ~20-40 сек…")
    photo = message.photo[-1]  # наибольшее разрешение
    file = await bot.get_file(photo.file_id)
    buf = await bot.download_file(file.file_path)
    image_bytes = buf.read()
    try:
        res = await asyncio.to_thread(service.create_meme_draft, image_bytes)
    except Exception as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    caps = res["captions"]
    text = "Обери підпис до мема:\n\n" + "\n\n".join(
        f"<b>{i + 1}.</b> {html.escape(c)}" for i, c in enumerate(caps)
    )
    await message.answer_photo(
        _local_photo(res["image_url"]),
        caption=text[:1024],
        reply_markup=kb.meme_captions_kb(res["post_id"], len(caps)),
    )


@router.callback_query(F.data.startswith("memecap:"))
async def meme_caption_pick(callback: CallbackQuery):
    _, post_id, idx = callback.data.split(":")
    caption = await asyncio.to_thread(service.choose_meme_caption, int(post_id), int(idx))
    if caption is None:
        await callback.answer("Не знайдено", show_alert=True)
        return
    await callback.answer("Обрано ✅")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"<b>[Мем]</b>\n\n{html.escape(caption)}",
        reply_markup=kb.draft_kb(int(post_id), with_image=False),
    )


# --- Свой пост ---------------------------------------------------------------

@router.message(F.text == kb.BTN_OWN_POST)
@router.message(Command("own"))
async def own_post_start(message: Message, state: FSMContext):
    await state.set_state(OwnPost.waiting_text)
    await message.answer("Надішли текст свого поста наступним повідомленням.")


@router.message(OwnPost.waiting_text, F.text)
async def own_post_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Надішли текст поста повідомленням.")
        return
    await state.clear()
    try:
        post_id = await asyncio.to_thread(service.add_own_post, message.text)
    except Exception as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    await message.answer(
        f"Пост #{post_id} збережено. Опублікувати?",
        reply_markup=kb.draft_kb(post_id, with_image=False),
    )
