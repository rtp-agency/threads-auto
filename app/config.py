from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Threads / Meta
    threads_app_id: str = ""
    threads_app_secret: str = ""
    threads_redirect_uri: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    # LLM: провайдер текстовой генерации — openai | xai (Grok) | anthropic
    llm_provider: str = "openai"
    openai_api_key: str = ""
    xai_api_key: str = ""
    anthropic_api_key: str = ""
    # дешёвая модель для классификации/фильтров и основная для генерации постов
    # gpt-5-nano слишком слаб для нюансного фильтра тем -> gpt-5-mini
    classifier_model: str = "gpt-5-mini"
    generation_model: str = "gpt-5-mini"

    # Embeddings: openai | voyage
    embedding_provider: str = "openai"
    voyage_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1024

    # Image generation (используется только для генерации аватарок;
    # POV-скриншоты собираются программно)
    image_gen_provider: str = "openai"
    image_gen_api_key: str = ""
    image_model: str = "gpt-image-2"  # самая свежая image-модель OpenAI
    # как делать скриншот-уведомление для POV: template (Pillow, точный формат,
    # бесплатно) | ai (gpt-image, реалистичнее, но квадрат и платно)
    notification_render_mode: str = "template"

    # Infra
    database_url: str = "postgresql+psycopg2://threads:threads@db:5432/threads"
    redis_url: str = "redis://redis:6379/0"
    token_encryption_key: str = ""

    # Trends
    news_source_url: str = "https://news.google.com/rss?hl=uk&gl=UA&ceid=UA:uk"

    # Media
    public_base_url: str = ""
    media_dir: str = "/data/media"

    # Reports
    report_weekday: int = 1
    report_hour: int = 10

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
