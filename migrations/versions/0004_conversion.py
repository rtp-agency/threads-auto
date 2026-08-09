"""conversion formats + lead tracking

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-27
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NEW_ARCHETYPES = ("transformation", "pain_point", "lead_magnet")


def upgrade() -> None:
    # ALTER TYPE ADD VALUE нельзя в транзакции -> autocommit_block
    with op.get_context().autocommit_block():
        for v in NEW_ARCHETYPES:
            op.execute(f"ALTER TYPE archetype ADD VALUE IF NOT EXISTS '{v}'")
    op.add_column(
        "posts",
        sa.Column("brought_lead", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_posts_brought_lead", "posts", ["brought_lead"])


def downgrade() -> None:
    op.drop_index("ix_posts_brought_lead", table_name="posts")
    op.drop_column("posts", "brought_lead")
    # значения enum не откатываем (ALTER TYPE ... DROP VALUE не поддерживается)
