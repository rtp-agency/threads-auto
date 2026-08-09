"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-14
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1024

post_source = sa.Enum("generated", "client_own", name="post_source")
archetype = sa.Enum(
    "pov_confession", "educational", "trend_reaction", "meme_notification",
    name="archetype",
)
post_status = sa.Enum("draft", "approved", "published", "rejected", name="post_status")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("threads_user_id", sa.String(64), unique=True, nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text, nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("threads_media_id", sa.String(64), nullable=True),
        sa.Column("source", post_source, nullable=False),
        sa.Column("archetype", archetype, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.Column("status", post_status, nullable=False, server_default="draft"),
        sa.Column("meta", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_posts_status", "posts", ["status"])

    op.create_table(
        "post_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("post_id", sa.Integer, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("views", sa.Integer, server_default="0"),
        sa.Column("likes", sa.Integer, server_default="0"),
        sa.Column("replies", sa.Integer, server_default="0"),
        sa.Column("reposts", sa.Integer, server_default="0"),
        sa.Column("quotes", sa.Integer, server_default="0"),
        sa.Column("engagement_rate", sa.Float, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_post_metrics_post_id", "post_metrics", ["post_id"])

    op.create_table(
        "account_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("followers_count", sa.Integer, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "voice_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("profile_text", sa.Text, nullable=False),
        sa.Column("examples", JSONB, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "post_embeddings",
        sa.Column("post_id", sa.Integer, sa.ForeignKey("posts.id"), primary_key=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
    )

    op.create_table(
        "trend_topics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("headline", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("relevance_score", sa.Float, server_default="0"),
        sa.Column("used", sa.Boolean, server_default="false"),
    )

    op.create_table(
        "avatar_pool",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("image_url", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("avatar_pool")
    op.drop_table("trend_topics")
    op.drop_table("post_embeddings")
    op.drop_table("voice_profile")
    op.drop_table("account_metrics")
    op.drop_table("post_metrics")
    op.drop_table("posts")
    op.drop_table("accounts")
    post_status.drop(op.get_bind())
    archetype.drop(op.get_bind())
    post_source.drop(op.get_bind())
