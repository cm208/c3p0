"""web dashboard sessions

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13 00:00:01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_username", sa.String(length=80), nullable=True),
        sa.Column("discord_avatar_hash", sa.String(length=64), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_permissions_cache", sa.JSON(), nullable=True),
        sa.Column("guild_permissions_cached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_sessions_token_hash", "web_sessions", ["token_hash"], unique=True)
    op.create_index("ix_web_sessions_discord_user_id", "web_sessions", ["discord_user_id"])
    op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_web_sessions_expires_at", table_name="web_sessions")
    op.drop_index("ix_web_sessions_discord_user_id", table_name="web_sessions")
    op.drop_index("ix_web_sessions_token_hash", table_name="web_sessions")
    op.drop_table("web_sessions")
