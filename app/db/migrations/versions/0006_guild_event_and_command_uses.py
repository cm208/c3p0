"""guild event feed + custom command use counts

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(length=12), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guild_event_guild_id_id", "guild_event", ["guild_id", "id"])

    # server_default so every existing row starts at 0 without a data
    # migration; batch mode because SQLite can't ALTER TABLE ADD COLUMN
    # with a constraint-bearing column in all versions.
    with op.batch_alter_table("custom_command") as batch:
        batch.add_column(sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("custom_command") as batch:
        batch.drop_column("use_count")
    op.drop_index("ix_guild_event_guild_id_id", table_name="guild_event")
    op.drop_table("guild_event")
