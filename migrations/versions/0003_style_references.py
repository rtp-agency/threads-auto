"""style_references table (референсы стиля для генерации скриншотов)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "style_references",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("image_b64", sa.Text, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_style_references_active", "style_references", ["active"])


def downgrade() -> None:
    op.drop_index("ix_style_references_active", table_name="style_references")
    op.drop_table("style_references")
