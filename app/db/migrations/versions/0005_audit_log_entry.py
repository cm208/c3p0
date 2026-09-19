"""audit log entry

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-13 00:00:03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log_entry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "ROLE_CREATE", "ROLE_EDIT", "ROLE_DELETE",
                "CHANNEL_CREATE", "CHANNEL_EDIT", "CHANNEL_DELETE",
                name="auditaction",
            ),
            nullable=False,
        ),
        sa.Column(
            "target_type",
            sa.Enum("ROLE", "CHANNEL", name="audittargettype"),
            nullable=False,
        ),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("target_name", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_entry_guild_id", "audit_log_entry", ["guild_id"])
    op.create_index(
        "ix_audit_log_entry_guild_id_created_at", "audit_log_entry", ["guild_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_entry_guild_id_created_at", table_name="audit_log_entry")
    op.drop_index("ix_audit_log_entry_guild_id", table_name="audit_log_entry")
    op.drop_table("audit_log_entry")
    sa.Enum(name="audittargettype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="auditaction").drop(op.get_bind(), checkfirst=True)
