"""Генерация черновиков постов (ТЗ п.7).

- провайдер задаётся LLM_PROVIDER (openai по умолчанию, anthropic — опция);
- few-shot: топ-3 похожих поста с лучшим engagement_rate из pgvector (ТЗ п.6);
- за один запрос /generate — 3-5 черновиков разных архетипов.
"""
from sqlalchemy import select, text as sa_text
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.embeddings import embed_one
from app.llm.providers import structured_completion
from app.models import Archetype, Post, PostStatus, VoiceProfile

# ════════════════════════════════════════════════════════════════════════════
# ⚙️  ГОЛОВНА ТОЧКА КАСТОМІЗАЦІЇ ПІД НОВОГО КЛІЄНТА
# ────────────────────────────────────────────────────────────────────────────
# BASE_SYSTEM задаёт НИШУ, ПЕРСОНУ, ЯЗЫК и ТОН генерации. Это шаблон:
# замени плейсхолдеры [У КВАДРАТНИХ ДУЖКАХ] на данные нового клиента.
# Что менять под клиента:
#   1) [ПЕРСОНА] — кто автор, ниша, язык, канал лидогенерации.
#   2) [РОЛЬ_СПІВРОЗМОВНИКА] — кто пишет в скриншотах (клиент/ученик/пациент…).
#   3) примеры pov-скриншотов, educational-форматов и конверсионных — под нишу.
#   4) набор эмодзи.
# Дословный «голос» и few-shot берутся из voice profile в БД (см. REUSE_GUIDE.md)
# — сюда хардкодить реальные посты клиента НЕ нужно.
# ════════════════════════════════════════════════════════════════════════════
# Стабильная часть system prompt — идёт первой, кэшируется вместе с voice profile.
BASE_SYSTEM = """\
Ти пишеш пости для Threads від імені [ПЕРСОНА: напр. «репетитора X, який пише
мовою Y; Threads — канал лідогенерації на його послугу/школу»]. Твоє завдання —
писати ТОЧНО його голосом, за описом голосу і прикладами нижче.

Категорично: НЕ згладжуй голос до ввічливого корпоративного тону. Пиши так, як
задано у профілі голосу (різкість / сленг / гумор / мат — рівно настільки,
наскільки вони є в голосі клієнта). НЕ будь мотиваційним ботом, НЕ моралізуй,
уникай кліше.

🔥 ГОЛОВНИЙ ФОРМАТ (pov_confession) — вірусний формат охоплення. Механіка:

- ПІДПИС (поле text) — це ОДИН короткий рядок, максимум одне речення.
  ⚠️ ГОЛОВНЕ: підпис має ІНТРИГУВАТИ, а НЕ спойлити зміст скріншота. Панчлайн —
  тільки у скріншоті. Чергуй різні заходи (не починай усі підписи однаково):
    • «[коротка інтрига]?»
    • «Пов: [коротка ситуація]. Ось що присилає:» / «…дивіться що прислав:»
    • «Коли [ситуація в одне речення]:»
    • «Це вже нове дно чи ще норм?»
    • «Оцініть рівень нахабства 🤡»
  Роби підписи РІЗНИМИ за структурою, а не переставляй ті самі слова.

- ВСЯ СІЛЬ І ГУМОР — у СКРІНШОТІ повідомлення (поле notification.message).
  ЦЕ ЗІРКА ПОСТА, сюди вся креативність: абсурдна, зухвала, смішна ситуація від
  [РОЛЬ_СПІВРОЗМОВНИКА: напр. «співрозмовника / клієнта»].
  [ЗАМІНИ на 2-3 приклади реальних топових скрінів нового клієнта — саме вони
   задають планку абсурду й гумору.]
  ⚠️ ЧЕРГУЙ РІЗНІ ТИПИ ситуацій у батчі (не роби всі про одне). Типи під нішу:
  відмазка, дивна прохалка, грошова халепа, побутовий абсурд, технічний фейл,
  лінь, дивний флекс, наглість про оплату, прохання про знижку, абсурдна причина.

- app: зазвичай "Telegram" (звичайне повідомлення). Іноді "monobank" — переказ.
- sender ЗАЛЕЖИТЬ від app:
    • Telegram: ім'я (+ роль, якщо доречно);
    • monobank: звичайне ім'я + КУМЕДНЕ прізвище, БЕЗ ролі. НЕ використовуй
      прізвище «Баланс» (воно збігається з міткою на скріні).
- Для monobank:
    • amount — смішна МАЛЕНЬКА сума (напр. "37.00₴", "13.00₴");
    • message — це КОМЕНТАР до переказу: природна зухвала/винувата фраза, як у
      реального переказу (вибачення + смішна причина маленької суми). НЕ роби суху
      мітку на кшталт «за послугу.».

Інші архетипи (БЕЗ зображення, notification=null) — РІЗНІ формати, чергуй їх:
- educational: живий корисний контент у ніші клієнта. Формати чергувати:
    розбір / лайфхак «Як зробити X» (2-3 варіанти) / цікавий факт з іронією /
    топ-список / relatable-POV ТЕКСТОМ — але ЗАВЕРШЕНИЙ, з готовим панчлайном
    у самому тексті.
- trend_reaction: коротка іронічна реакція на актуальну подію через призму ніші.

КОНВЕРСІЙНІ формати (мета — м'яко привести до цільової дії; notification=null).
Це НЕ реклама в лоб (вона вбиває охоплення) — той самий живий тон, але з гачком:
- transformation: результат клієнта «до → після» (показує, що з автором реально
  прокачуються; без прямого продажу).
- pain_point: біль → рішення. ⚠️ ЧЕРГУЙ РІЗНІ болі (не повторюй одну й ту саму),
  бери різні реальні болі аудиторії ніші. Позиціонує автора як рішення, без
  прямого продажу.
- lead_magnet: безкоштовний оффер за дію (збір лідів у директ / коменти).

ПРАВИЛО ПРО ЗОБРАЖЕННЯ: notification заповнюй ТІЛЬКИ для pov_confession. Для
educational і trend_reaction — notification = null.

📌 ПЕРШИЙ КОМЕНТАР (поле first_comment) — для educational-постів типу «Як зробити/
сказати X», де відповідь/розшифровку логічно винести в закріплений коментар (це
тримає людину в пості й дає охоплення). Для таких постів: text = гачок/питання
БЕЗ відповіді; first_comment = сама відповідь/розшифровка + короткий приклад. Для
ВСІХ інших постів first_comment = null.

Вимоги до кожного поста:
- ⚠️ ТЕКСТОВІ пости (educational, trend_reaction, конверсійні) мають бути
  ЗАВЕРШЕНИМИ — панчлайн у самому тексті. НІКОЛИ не закінчуй пост обірваним
  «Я:», «Також я:» чи «:» у кінці, що чекає на картинку-реакцію — таких картинок
  ми НЕ генеруємо. Формат-скріншот = ТІЛЬКИ pov_confession.
- КОРОТКО і рубано. 1-3 короткі рядки або одне провокативне питання. НЕ пиши
  довгих есе, НЕ пояснюй як зануда.
- структура: гачок -> коротка сценка/провокація -> питання до аудиторії;
- використовуй емодзі як гачок і паузу ([ПІДБЕРИ набір під нішу, напр. 😏 👇 🤡]);
- жива розмовна мова, сленг там, де це стиль клієнта;
- без хештегів.

Орієнтуйся на ДОСЛІВНІ приклади голосу нижче як на еталон довжини, ритму й тону —
пиши так само коротко і живо, а не як нейромережа."""

DRAFTS_SCHEMA = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "archetype": {
                        "type": "string",
                        "enum": [a.value for a in Archetype],
                    },
                    "text": {"type": "string"},
                    # null для всех архетипов, кроме pov_confession
                    "notification": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "app": {"type": "string"},
                                    "sender": {"type": "string"},
                                    "message": {"type": "string"},
                                    # для monobank — сумма перевода, иначе null
                                    "amount": {"type": ["string", "null"]},
                                },
                                "required": ["app", "sender", "message", "amount"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ]
                    },
                    # первый комментарий (перевод/ответ), который клиент закрепляет
                    # под постом — для educational «как сказать…»; иначе null
                    "first_comment": {"type": ["string", "null"]},
                },
                "required": ["archetype", "text", "notification", "first_comment"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["drafts"],
    "additionalProperties": False,
}


def get_voice_profile(session: Session, account_id: int) -> VoiceProfile | None:
    return session.scalar(
        select(VoiceProfile)
        .where(VoiceProfile.account_id == account_id)
        .order_by(VoiceProfile.updated_at.desc())
        .limit(1)
    )


def retrieve_similar_top_posts(
    session: Session, query: str, limit: int = 3, candidates: int = 10
) -> list[Post]:
    """Топ-3 похожих опубликованных поста с лучшим engagement_rate (ТЗ п.6)."""
    query_vec = embed_one(query, input_type="query")
    rows = session.execute(
        sa_text(
            """
            SELECT p.id
            FROM post_embeddings pe
            JOIN posts p ON p.id = pe.post_id
            LEFT JOIN LATERAL (
                SELECT engagement_rate FROM post_metrics
                WHERE post_id = p.id ORDER BY fetched_at DESC LIMIT 1
            ) m ON true
            WHERE p.status = 'published'
            ORDER BY pe.embedding <=> CAST(:vec AS vector)
            LIMIT :candidates
            """
        ),
        {"vec": str(query_vec), "candidates": candidates},
    ).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return []
    posts = session.scalars(select(Post).where(Post.id.in_(ids))).all()

    def er(post: Post) -> float:
        latest = max(post.metrics, key=lambda m: m.fetched_at, default=None)
        return latest.engagement_rate if latest else 0.0

    return sorted(posts, key=er, reverse=True)[:limit]


def generate_meme_captions(
    session: Session, account_id: int, image_bytes: bytes, n: int = 3
) -> list[str]:
    """По загруженному фото-мему генерирует n подписей голосом клиента (vision).
    Роутится по провайдеру (Grok тоже умеет vision)."""
    from app.llm.providers import vision_json

    profile = get_voice_profile(session, account_id)
    if profile is None:
        raise RuntimeError("Voice profile не найден.")
    good_rated, _ = get_rated_examples(session, account_id)
    examples_block = "\n\n---\n\n".join(list(profile.examples or []) + good_rated)
    voice_block = (
        f"ОПИС ГОЛОСУ:\n{profile.profile_text}\n\n"
        f"ДОСЛІВНІ ПРИКЛАДИ (еталон тону й довжини):\n{examples_block}"
    )
    schema = {
        "type": "object",
        "properties": {"captions": {"type": "array", "items": {"type": "string"}}},
        "required": ["captions"],
        "additionalProperties": False,
    }
    user_text = (
        f"Це мем-картинка для Threads-поста. Подивись, що на ній, і придумай {n} "
        f"РІЗНИХ коротких підписів його голосом — так, щоб підпис і картинка разом "
        f"були смішні й у його стилі (провокація, іронія, зв'язок з нішею клієнта). "
        f"Коротко, з емодзі де доречно. Поверни JSON {{captions: [...]}}."
    )
    data = vision_json(BASE_SYSTEM + "\n\n" + voice_block, user_text, image_bytes, schema)
    return data["captions"][:n]


SANITIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "app": {"type": "string"},
        "sender": {"type": "string"},
        "message": {"type": "string"},
        "amount": {"type": ["string", "null"]},
    },
    "required": ["app", "sender", "message", "amount"],
    "additionalProperties": False,
}

SANITIZE_SYSTEM = """\
Ти редагуєш дані скріншота-повідомлення від співрозмовника (для POV-поста клієнта).
Дано ПОТОЧНІ поля скріншота і вільну інструкцію автора, що змінити. Поверни
ОНОВЛЕНІ поля (app, sender, message, amount).

ПРАВИЛА:
- Зміни ЛИШЕ те, що просить автор; решту залиш РІВНО як є.
- Якщо автор просто дав новий текст без пояснень — це новий message (для Telegram)
  або новий коментар до переказу (для monobank, app=monobank).
- Навіть якщо інструкція корява/неохайна — приведи її до нормального вигляду:
  message має бути природним, читабельним повідомленням співрозмовника у його стилі
  (живо, можна сленг/мат, з емодзі де доречно), а НЕ буквальний переказ інструкції.
- НЕ ламай формат: app лишається (Telegram/monobank), для monobank amount — коротка
  сума (напр. "37.00₴"), sender — адекватне ім'я (для monobank без слова «учень»).
- Не залишай поле порожнім і не пиши службових слів («змінити», «текст:») у message."""


def sanitize_notification(current: dict, instruction: str) -> dict:
    """Приводит вольную инструкцию учителя к чистым полям скриншота, меняя только
    нужное и сохраняя валидный формат (чтобы картинка не сломалась)."""
    import json as _json

    user = (
        f"ПОТОЧНІ поля:\n{_json.dumps(current, ensure_ascii=False)}\n\n"
        f"ІНСТРУКЦІЯ автора (що змінити):\n{instruction}"
    )
    return structured_completion(
        model=settings.generation_model,
        system_blocks=[SANITIZE_SYSTEM],
        user_text=user,
        schema=SANITIZE_SCHEMA,
        max_tokens=2000,
    )


IDEA_SYSTEM = """\
Ти створюєш дані скріншота-повідомлення для POV-поста за ВІЛЬНИМ описом автора
(яка ситуація, хто пише, що пише, скільки грошей). Поверни поля
app/sender/message/amount.

ПРАВИЛА:
- Визнач app сам: якщо це грошовий переказ — app="monobank" і amount (коротка
  смішна сума, напр. "37.00₴"), message = коментар до переказу; інакше
  app="Telegram", amount=null, message = повідомлення співрозмовника.
- Навіть якщо опис корявий/короткий — зроби message ПРИРОДНИМ, живим, смішним
  повідомленням у стилі клієнта, а НЕ буквальним переказом опису. Розкрий дотепно.
- sender: для Telegram — ім'я (+ роль, якщо доречно); для monobank — звичайне
  ім'я + кумедне прізвище, БЕЗ ролі. НЕ прізвище «Баланс».
- Нічого не залишай порожнім, без службових слів у message."""


def notification_from_idea(idea: str) -> dict:
    """Строит поля скриншота (app/sender/message/amount) из свободной идеи автора
    для ручного POV-поста."""
    return structured_completion(
        model=settings.generation_model,
        system_blocks=[IDEA_SYSTEM],
        user_text=f"ІДЕЯ:\n{idea}",
        schema=SANITIZE_SCHEMA,
        max_tokens=2000,
    )


SEQUEL_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "notification": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "sender": {"type": "string"},
                "message": {"type": "string"},
                "amount": {"type": ["string", "null"]},
            },
            "required": ["app", "sender", "message", "amount"],
            "additionalProperties": False,
        },
    },
    "required": ["caption", "notification"],
    "additionalProperties": False,
}

SEQUEL_SYSTEM = """\
Ти пишеш ПРОДОВЖЕННЯ (сиквел) вірусного POV-поста клієнта.
Дано попередній пост: коротку підпис-питання і скріншот повідомлення того самого
співрозмовника. Твоє завдання — наступна серія тієї ж історії з ТИМ САМИМ співрозмовником.

ПРАВИЛА:
- app і sender (той самий учень) — ЗАЛИШ РІВНО ТІ САМІ, що в попередньому.
- notification.message — НАСТУПНЕ повідомлення від цього ж співрозмовника: логічне продовження
  або ескалація абсурду («минув тиждень — а він…», «а сьогодні прилетіло таке»).
  Живо, у його стилі, можна сленг/мат/емодзі. Це «зірка» поста.
- Для monobank постав amount (смішна маленька сума) і message = коментар до переказу.
- caption — КОРОТКА нова підпис-питання чи зачин у стилі автора (не повторюй
  дослівно попередню; натякни, що це продовження — «а ось і фінал», «частина 2»).
- Не пояснюй, не додавай службових слів. Все українською."""


def generate_sequel(session: Session, account_id: int, prev_caption: str, prev_notification: dict) -> dict:
    """Продолжение серии с ТЕМ ЖЕ учеником: новый caption + следующее сообщение
    того же отправителя (app/sender сохраняются)."""
    import json as _json

    profile = get_voice_profile(session, account_id)
    voice = f"ГОЛОС КЛІЄНТА:\n{profile.profile_text}" if profile else ""
    user = (
        f"ПОПЕРЕДНІЙ ПОСТ.\nПідпис: {prev_caption}\n"
        f"Скріншот співрозмовника: {_json.dumps(prev_notification, ensure_ascii=False)}\n\n"
        f"Напиши наступну серію з тим самим співрозмовником."
    )
    return structured_completion(
        model=settings.generation_model,
        system_blocks=[SEQUEL_SYSTEM] + ([voice] if voice else []),
        user_text=user,
        schema=SEQUEL_SCHEMA,
        max_tokens=2000,
    )


def get_rated_examples(session: Session, account_id: int) -> tuple[list[str], list[str]]:
    """Возвращает (good, bad) — тексты постов, размеченных вручную (ТЗ: обучение)."""
    good = session.scalars(
        select(Post.text)
        .where(Post.account_id == account_id, Post.voice_rating == "good")
        .order_by(Post.created_at.desc())
        .limit(10)
    ).all()
    bad = session.scalars(
        select(Post.text)
        .where(Post.account_id == account_id, Post.voice_rating == "bad")
        .order_by(Post.created_at.desc())
        .limit(6)
    ).all()
    return list(good), list(bad)


def generate_drafts(
    session: Session,
    account_id: int,
    n_drafts: int = 4,
    trend_headline: str | None = None,
) -> list[dict]:
    """Генерирует n_drafts черновиков разных архетипов.

    Возвращает [{archetype, text, notification?}].
    """
    profile = get_voice_profile(session, account_id)
    if profile is None:
        raise RuntimeError(
            "Voice profile не найден. Сначала выполните онбординг (см. scripts/ и docs/)."
        )

    # эталонные примеры = курированные из профиля + вручную одобренные (обучение)
    good_rated, bad_rated = get_rated_examples(session, account_id)
    all_examples = list(profile.examples or []) + good_rated
    examples_block = "\n\n---\n\n".join(all_examples)
    voice_block = (
        f"ОПИС ГОЛОСУ КЛІЄНТА:\n{profile.profile_text}\n\n"
        f"ДОСЛІВНІ ПРИКЛАДИ ПОСТІВ КЛІЄНТА (few-shot, ЕТАЛОН — пиши так само):\n"
        f"{examples_block}"
    )
    if bad_rated:
        anti_block = "\n\n---\n\n".join(bad_rated)
        voice_block += (
            "\n\n❌ ПРИКЛАДИ НЕВДАЛИХ ПОСТІВ (НЕ його голос — НЕ пиши так, уникай "
            f"такого тону, довжини і структури):\n{anti_block}"
        )

    # Few-shot retrieval: похожие топ-посты по теме запроса
    retrieval_query = trend_headline or "особиста історія клієнта, провокація"
    try:
        similar = retrieve_similar_top_posts(session, retrieval_query)
    except Exception:
        similar = []  # retrieval — усилитель, не блокер генерации
    similar_block = ""
    if similar:
        joined = "\n\n---\n\n".join(p.text for p in similar)
        similar_block = (
            "\nПОСТИ КЛІЄНТА З НАЙКРАЩИМ ENGAGEMENT, СХОЖІ НА ТЕМУ ЗАПИТУ "
            "(орієнтуйся на їхню подачу):\n" + joined
        )

    # анти-повтор: последние сгенерированные посты, чтобы не крутить одно и то же
    recent = session.scalars(
        select(Post.text)
        .where(Post.account_id == account_id, Post.text.isnot(None))
        .order_by(Post.created_at.desc())
        .limit(25)
    ).all()
    recent_block = ""
    if recent:
        joined = "\n---\n".join(t[:160].replace("\n", " ") for t in recent if t)
        recent_block = (
            "\n\n🚫 НЕДАВНО ВЖЕ ПОСТИЛИ (НЕ повторюй ці ситуації, зачини, жарти й "
            "формулювання — вигадай ЗОВСІМ інші):\n" + joined
        )

    n_pov = max(1, round(n_drafts * 0.6))
    n_conv = max(1, round(n_drafts * 0.2))
    n_val = max(1, n_drafts - n_pov - n_conv)
    task = (
        f"Згенеруй {n_drafts} чернеток постів РІЗНИХ форматів (це важливо — не роби "
        f"все однакове). Пропорція:\n"
        f"- ~{n_pov} pov_confession (охоплення): коротка підпис-питання + скріншот "
        f"абсурдного повідомлення співрозмовника. Для кожного СВІЖА, несподівана ситуація "
        f"(чергуй типи!), не повторюй приклади дослівно.\n"
        f"- ~{n_conv} КОНВЕРСІЙНИХ (transformation / pain_point / lead_magnet) — "
        f"м'яко ведуть на урок, чергуй їх.\n"
        f"- ~{n_val} value: educational різних форматів (розбір, лайфхак, факт, "
        f"топ-список, текстовий POV) або trend_reaction.\n"
        f"Пам'ятай: у pov підпис короткий, а сила — у повідомленні співрозмовника.\n"
    )
    if trend_headline:
        task += f"\nДля trend_reaction (якщо буде) використай тему: «{trend_headline}»\n"
    else:
        task += "\nАрхетип trend_reaction цього разу НЕ використовуй (немає теми).\n"
    task += (
        "\nnotification заповнюй ТІЛЬКИ для pov_confession (app: Telegram або "
        "monobank; для monobank став amount — смішну маленьку суму). Для інших "
        "архетипів notification = null."
    )

    data = structured_completion(
        model=settings.generation_model,
        system_blocks=[BASE_SYSTEM, voice_block],
        user_text=similar_block + recent_block + "\n\n" + task,
        schema=DRAFTS_SCHEMA,
        max_tokens=8000,
        reasoning_effort="medium",  # для точной передачи голоса, не только скорость
    )
    return data["drafts"][:n_drafts]


def create_draft_posts(
    session: Session, account_id: int, drafts: list[dict]
) -> list[Post]:
    """Сохраняет сгенерированные черновики в posts со статусом draft."""
    from app.models import PostSource

    posts = []
    for d in drafts:
        post = Post(
            account_id=account_id,
            source=PostSource.generated,
            archetype=Archetype(d["archetype"]),
            text=d["text"],
            status=PostStatus.draft,
        )
        session.add(post)
        posts.append(post)
    session.flush()
    return posts
