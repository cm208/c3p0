"""music idle-disconnect timeout

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default so every existing guild starts at the 5-minute default
    # without a data migration; batch mode for SQLite, as in 0006.
    with op.batch_alter_table("music_config") as batch:
        batch.add_column(
            sa.Column("idle_disconnect_minutes", sa.Integer(), nullable=False, server_default="5")
        )


def downgrade() -> None:
    with op.batch_alter_table("music_config") as batch:
        batch.drop_column("idle_disconnect_minutes")
