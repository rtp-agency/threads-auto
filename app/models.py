import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base


class PostSource(str, enum.Enum):
    generated = "generated"
    client_own = "client_own"


class Archetype(str, enum.Enum):
    pov_confession = "pov_confession"
    educational = "educational"
    trend_reaction = "trend_reaction"
    meme_notification = "meme_notification"
    # конверсионные форматы (мягко ведут на уроки)
    transformation = "transformation"   # результат/трансформация ученика
    pain_point = "pain_point"           # боль -> решение (позиционирование)
    lead_magnet = "lead_magnet"         # бесплатный оффер за DM/коммент


class PostStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    published = "published"
    rejected = "rejected"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    threads_user_id: Mapped[str] = mapped_column(String(64), unique=True)
    username: Mapped[str] = mapped_column(String(255))
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    posts: Mapped[list["Post"]] = relationship(back_populates="account")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    threads_media_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[PostSource] = mapped_column(Enum(PostSource, name="post_source"))
    archetype: Mapped[Archetype | None] = mapped_column(
        Enum(Archetype, name="archetype"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, name="post_status"), default=PostStatus.draft
    )
    # доп. данные генерации: для meme_notification — {app, sender, message},
    # нужно для ручной перегенерации картинки кнопкой в боте
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ручная разметка голоса (обучение few-shot): 'good' | 'bad' | None
    voice_rating: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # пометка «пост привёл запрос/лид» (аналитика конверсии)
    brought_lead: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    account: Mapped[Account] = relationship(back_populates="posts")
    metrics: Mapped[list["PostMetric"]] = relationship(back_populates="post")


class PostMetric(Base):
    """Снапшоты метрик во времени (не перезапись) — для скорости роста 2ч/6ч/24ч."""

    __tablename__ = "post_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    replies: Mapped[int] = mapped_column(Integer, default=0)
    reposts: Mapped[int] = mapped_column(Integer, default=0)
    quotes: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0.0)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    post: Mapped[Post] = relationship(back_populates="metrics")


class AccountMetric(Base):
    """Снапшоты по аккаунту (подписчики) — для отчёта о росте аудитории."""

    __tablename__ = "account_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    followers_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class VoiceProfile(Base):
    __tablename__ = "voice_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    profile_text: Mapped[str] = mapped_column(Text)
    examples: Mapped[list] = mapped_column(JSONB, default=list)  # verbatim цитаты (few-shot)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PostEmbedding(Base):
    __tablename__ = "post_embeddings"

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), primary_key=True)
    embedding = mapped_column(Vector(settings.embedding_dim))


class TrendTopic(Base):
    __tablename__ = "trend_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    headline: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AvatarPool(Base):
    __tablename__ = "avatar_pool"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StyleReference(Base):
    """Референсы визуального стиля для генерации скриншотов-мокапов.

    image_b64 — картинка (PNG, ужата до ~1024px) в base64; хранится в БД, чтобы
    не выкладывать материал публично. source: 'archive' (реальные скриншоты
    клиента) | 'approved' (сгенерированные, одобренные в /train).
    """

    __tablename__ = "style_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_b64: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
