"""bot guild membership

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_guild",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("is_present", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bot_guild")
