# Threads Automation

Система автоматизации контента и аналитики для Threads: сбор метрик → анализ
голоса → генерация черновиков в стиле клиента → апрув через Telegram-бот →
автопостинг.

> 🆕 **Переносишь под нового клиента? Начни с [REUSE_GUIDE.md](REUSE_GUIDE.md)** —
> там пошагово: ключи, деплой, онбординг голоса и **кастомизация промптов под нишу**.
> Это обезличенный шаблон: секретов нет, ниша задаётся плейсхолдерами.

Стек: FastAPI + Celery + PostgreSQL (pgvector) + Redis, aiogram 3; текст — OpenAI /
xAI (Grok) / Anthropic на выбор (`LLM_PROVIDER`), картинки — gpt-image.

## Архитектура

| Сервис    | Роль |
|-----------|------|
| `api`     | FastAPI: OAuth callback Threads, раздача сгенерированных картинок (`/media`) |
| `bot`     | Telegram-бот (aiogram 3): статистика, черновики, апрув, свой пост |
| `worker`  | Celery: публикация, сбор метрик, тренды, отчёты |
| `beat`    | Celery beat: расписание (метрики, рефреш токенов, тренды, недельный отчёт) |
| `db`      | PostgreSQL 16 + pgvector (few-shot retrieval) |
| `redis`   | Брокер Celery |

Каскад моделей (ТЗ п.7), провайдер переключается `LLM_PROVIDER`:
- **openai** (по умолчанию): `gpt-5-nano` — фильтрация новостей,
  `gpt-5-mini` — генерация постов (для качества можно поднять до `gpt-5`);
- **anthropic**: `claude-haiku-4-5` / `claude-sonnet-5` (+ prompt caching
  voice profile).

Эмбеддинги — `EMBEDDING_PROVIDER`: OpenAI `text-embedding-3-small`
(dimensions=1024, по умолчанию) или Voyage `voyage-3.5`.

## Запуск

```bash
cp .env.example .env   # заполнить секреты
# TOKEN_ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build
```

Миграции применяются автоматически (сервис `migrate`).

## Онбординг клиента (по порядку)

1. **Meta App**: создать приложение с use case "Access the Threads API",
   добавить клиента как **Threads Tester** (по username), клиент принимает
   инвайт в настройках Threads. Прописать `THREADS_REDIRECT_URI` в приложении.
2. **OAuth**: отправить клиенту `https://<домен>/auth/threads/login` →
   после авторизации long-lived токен (60 дней) сохраняется зашифрованным.
   Рефреш — автоматически (celery beat, ежедневно).
3. **Voice profile** (разовая ручная операция, ТЗ п.5 — НЕ через API сервиса):
   ```bash
   docker compose run --rm api python scripts/fetch_threads_archive.py
   # threads_archive.json + docs/voice_profile_prompt.md -> Claude -> вычитка
   docker compose run --rm api python scripts/save_voice_profile.py \
       --profile voice_profile.md --examples examples.json
   ```
4. **Пул аватаров** (разово, для meme_notification):
   ```bash
   docker compose run --rm api python scripts/generate_avatar_pool.py --count 18
   ```
5. Клиент пишет боту `/start`; его `chat_id` → `TELEGRAM_ADMIN_CHAT_ID` в `.env`.

## Бот

- `/start` — меню: 📊 Статистика / 📝 Чернетки постів / ➕ Свій пост
- **Черновики**: бот предлагает 2-3 отфильтрованные трендовые темы (или
  генерацию без тренда), генерирует 4 черновика разных архетипов
  (`pov_confession`, `educational`, `trend_reaction`, `meme_notification`).
  Каждый черновик — карточка (текст + картинка для meme_notification) с
  кнопками: ✅ Опубликовать / ✏️ Редактировать / ❌ Отклонить /
  🔄 Перегенерировать фото.
- **Свой пост**: текст клиента идёт в ту же очередь публикации и в метрики
  наравне со сгенерированными.
- **Апрув → публикация**: ✅ ставит Celery-задачу сразу (SLA ≤ 15 минут);
  публикация — create container → ~30 сек → publish.
- **Недельный отчёт** приходит проактивно (день/час — `REPORT_WEEKDAY`/`REPORT_HOUR`).

## Расписание (celery beat, Europe/Kyiv)

| Задача | Когда |
|---|---|
| Рефреш Threads-токенов | ежедневно 03:00 (когда до истечения < 10 дней) |
| Метрики: все посты + подписчики | ежедневно 06:00 |
| Метрики: свежие посты (<24ч) | каждые 2 часа (скорость роста 2ч/6ч/24ч) |
| Тренды: RSS → Haiku-фильтр → 2-3 темы | ежедневно 08:00 |
| Недельный отчёт | по `REPORT_WEEKDAY`/`REPORT_HOUR` |

## Проверка критериев приёмки

- Список постов / тестовая публикация: `scripts/fetch_threads_archive.py`;
  «Свой пост» в боте → ✅ → пост в Threads.
- Темы под trend_reaction: `docker compose exec worker celery -A app.tasks:celery_app call app.tasks.trends.fetch_trend_topics`, затем 📝 в боте.
- Метрики: таблица `post_metrics` пополняется снапшотами ≥ 1 раза в сутки.
- Отчёт: `... celery call app.tasks.reports.send_weekly_report` — придёт в чат.

## Вне скоупа MVP

Другие платформы, мониторинг конкурентов, CRM/платёжки, автовалидация текста
на картинках (только ручная кнопка перегенерации) — см. ТЗ.
