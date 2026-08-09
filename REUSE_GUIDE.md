# Гайд: как перенастроить систему под нового клиента

Это обезличенный шаблон системы автоматизации Threads (генерация постов в стиле
клиента → апрув в Telegram-боте → автопостинг + аналитика). Здесь — как поднять
его для **нового пользователя** с нуля.

> Все секреты предыдущего клиента удалены. В репозитории нет `.env`, только
> `.env.example`. Промпты обезличены (ниша задаётся плейсхолдерами — см. §5).

---

## 1. Что понадобится (аккаунты и ключи)

| Что | Где взять | Куда в `.env` |
|---|---|---|
| Домен + HTTPS | любой хостинг/VPS с доменом | `PUBLIC_BASE_URL`, `THREADS_REDIRECT_URI` |
| Meta App (Threads API) | developers.facebook.com → use case «Access the Threads API» | `THREADS_APP_ID`, `THREADS_APP_SECRET` |
| Telegram-бот | @BotFather → `/newbot` | `TELEGRAM_BOT_TOKEN` |
| LLM-ключ (текст) | OpenAI / xAI (Grok) / Anthropic | `OPENAI_API_KEY` или `XAI_API_KEY` |
| Ключ для картинок | OpenAI (gpt-image) | `IMAGE_GEN_API_KEY` |
| Ключ эмбеддингов | OpenAI / Voyage | тот же `OPENAI_API_KEY` / `VOYAGE_API_KEY` |

Провайдер текста выбирается `LLM_PROVIDER` (`openai` | `xai` | `anthropic`).
Для резкого/юморного тона обычно берут **xai (Grok)** — он менее цензурирован.

---

## 2. Установка

```bash
cp .env.example .env          # заполнить своими ключами (см. §1)

# сгенерировать ключ шифрования токенов и вписать в TOKEN_ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build  # миграции применяются автоматически (сервис migrate)
```

Сервисы: `api` (OAuth + раздача картинок), `bot` (Telegram), `worker`/`beat`
(Celery), `db` (Postgres+pgvector), `redis`.

---

## 3. Подключение аккаунта клиента (Threads OAuth)

1. В Meta App добавь клиента как **Threads Tester** (по username); клиент
   принимает инвайт в настройках Threads.
2. Пропиши `THREADS_REDIRECT_URI` = `https://<домен>/auth/threads/callback` в
   приложении Meta.
3. Дай клиенту ссылку `https://<домен>/auth/threads/login` → после логина
   long-lived токен (60 дней) сохраняется зашифрованным, рефреш — автоматом.
4. Клиент пишет боту `/start`; его `chat_id` впиши в `TELEGRAM_ADMIN_CHAT_ID`.

---

## 4. Онбординг «голоса» и стиля клиента (разово)

Система учится на реальном контенте клиента. После OAuth выполни:

```bash
# 1) выгрузить архив постов клиента
docker compose run --rm api python scripts/fetch_threads_archive.py

# 2) построить профиль голоса (анализ стиля) и сохранить в БД
docker compose run --rm api python scripts/build_voice_profile.py     # -> voice_profile.md
docker compose run --rm api python scripts/save_voice_profile.py \
    --profile voice_profile.md --examples examples.json               # examples.json = 3-7 лучших постов дословно

# 3) залить архив в pgvector для few-shot retrieval (похожие удачные посты)
docker compose run --rm api python scripts/import_archive_for_retrieval.py

# 4) референсы для генерации картинок-скриншотов (берёт реальные скрины клиента)
docker compose run --rm api python scripts/build_style_references.py --max 6
#    отдельно для monobank-переводов, если они есть в архиве:
#    ... build_style_references.py с source="monobank" (см. код скрипта)

# 5) пул синтетических аватаров (для template-режима картинок)
docker compose run --rm api python scripts/generate_avatar_pool.py --count 18
```

> `threads_archive.json`, `voice_profile.md`, `examples.json` — персональные
> данные клиента, они в `.gitignore` и не должны попадать в git.

---

## 5. ⭐ Кастомизация под нишу (главное!)

Промпты обезличены — под новую нишу отредактируй **плейсаолдеры** `[В КВАДРАТНЫХ
СКОБКАХ]`. Порядок по важности:

### 5.1 `app/llm/generation.py` → `BASE_SYSTEM` (главный файл)
Это «мозг» генерации. Замени:
- `[ПЕРСОНА: ...]` — кто клиент, ниша, язык, что он продаёт (напр. «нутрициолог,
  пишет на русском, Threads — воронка на консультации»).
- `[РОЛЬ_СПІВРОЗМОВНИКА: ...]` — кто пишет в скриншотах (ученик / клиент / пациент…).
- `[ЗАМІНИ на 2-3 приклади ...]` — 2-3 реальных топовых скриншота нового клиента
  (они задают планку абсурда/юмора для POV-формата).
- `[ПІДБЕРИ набір під нішу ...]` — эмодзи под клиента.

Там же (уже обезличены, но проверь тон): `SANITIZE_SYSTEM`, `SEQUEL_SYSTEM`,
`retrieval_query` (строка со значением по умолчанию для few-shot).

### 5.2 `app/llm/trends.py` → `FILTER_SYSTEM`
Фильтр новостей под тренд-посты. Логика (отсекать продуктовые запуски, трагедии,
рутинную политику; пропускать виральные эмоциональные события) — универсальна.
Подправь только упоминание ниши и **`NEWS_SOURCE_URL`** в `.env` (язык/регион RSS,
по умолчанию украинский Google News).

### 5.3 `app/bot/guide.py` → `GUIDE_PARTS`
Это методичка, которую видит клиент по кнопке 📖 Інструкція. Поправь формулировки
под услугу клиента (сейчас нейтрально: «цільова дія / послуга»).

### 5.4 Язык
Все промпты и тексты бота — на **украинском**. Если новый клиент на другом языке —
переведи `BASE_SYSTEM`, `trends.py`, `guide.py` и строки бота в `app/bot/handlers.py`.

### 5.5 Архетипы (опционально)
Форматы постов — в `Archetype` (`app/models.py`). Базовые (`pov_confession`,
`educational`, `trend_reaction`, `meme_notification`) + конверсионные
(`transformation`, `pain_point`, `lead_magnet`) подходят большинству ниш. Менять
набор нужно редко; если меняешь — не забудь про миграцию enum.

---

## 6. Работа в боте

После настройки клиент работает сам. Полная методичка — прямо в боте по кнопке
**📖 Інструкція** (или `/help`). Кратко:
- **📝 Чернетки постів** — сгенерировать пачку на апрув (тренд-тема / своя тема / без темы).
- Под карточкой: ✅ Опублікувати · ✏️ Редагувати · ❌ Відхилити;
  для POV-скринов: 🔄 Перегенерувати фото (другой персонаж) · ✏️ Змінити текст
  на фото (тот же персонаж, меняется текст/сумма) · 🔁 Продовження (серия с тем же).
- **🔁 Продовжити пост** — сиквел к прошлому посту с тем же персонажем.
- **➕ Свій пост** — свой готовый текст в очередь.
- **📊 Статистика** — охваты, вовлечённость, топ-посты, разбивка по форматам.
- **😂 Мем** — прислать фото → 3 варианта подписи.
- `/dataset` — обучение голосу (оценки ✅/❌), `/leads` — аналитика конверсии.

---

## 7. Безопасность (обязательно)

- **Никогда не коммить `.env`** (уже в `.gitignore`).
- После настройки **ротируй ключи**, если они где-то засветились (чат, скриншоты).
- Персональные данные клиента (`threads_archive.json`, `voice_profile.md`,
  `examples.json`, содержимое БД, media) — не выкладывай в публичные репозитории.
- `TOKEN_ENCRYPTION_KEY` уникален на инсталляцию: потеряешь — слетят сохранённые
  Threads-токены (нужен будет повторный OAuth).

---

## Чек-лист запуска нового клиента

- [ ] `.env` заполнен, `TOKEN_ENCRYPTION_KEY` сгенерирован
- [ ] `docker compose up -d --build` поднялся
- [ ] Meta App + Threads Tester + OAuth-логин клиента пройден
- [ ] `chat_id` клиента в `TELEGRAM_ADMIN_CHAT_ID`
- [ ] Voice profile + retrieval + style refs + avatars залиты (§4)
- [ ] `BASE_SYSTEM` и `trends.py` перенастроены под нишу (§5)
- [ ] `guide.py` отредактирован под услугу клиента
- [ ] Тест: 📝 Чернетки → пост выглядит «в голосе» клиента → ✅ публикуется
