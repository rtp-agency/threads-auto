"""add voice_rating to posts (ручная разметка голоса)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("voice_rating", sa.String(8), nullable=True))
    op.create_index("ix_posts_voice_rating", "posts", ["voice_rating"])


def downgrade() -> None:
    op.drop_index("ix_posts_voice_rating", table_name="posts")
    op.drop_column("posts", "voice_rating")
