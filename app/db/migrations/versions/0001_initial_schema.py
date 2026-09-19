"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild_config",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("prefix", sa.String(length=10), nullable=False, server_default="!"),
        sa.Column("default_role_id", sa.BigInteger(), nullable=True),
        sa.Column("log_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("moderation_log_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "welcome_config",
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("message_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("message_template", sa.Text(), nullable=True),
        sa.Column("embed_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("embed_title", sa.String(length=256), nullable=True),
        sa.Column("embed_description", sa.Text(), nullable=True),
        sa.Column("embed_footer", sa.String(length=256), nullable=True),
        sa.Column("dm_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dm_template", sa.Text(), nullable=True),
        sa.Column("role_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("role_id", sa.BigInteger(), nullable=True),
        sa.Column("join_log_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "role_binding",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "interaction_type",
            sa.Enum("REACTION", "BUTTON", "SELECT", name="rolebindingtype"),
            nullable=False,
        ),
        sa.Column("source_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.String(length=64), nullable=True),
        sa.Column("component_custom_id", sa.String(length=100), nullable=True),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("toggle", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_role_binding_guild_id", "role_binding", ["guild_id"])
    op.create_index("ix_role_binding_source_message_id", "role_binding", ["source_message_id"])

    op.create_table(
        "infraction",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("WARN", "TIMEOUT", "KICK", "BAN", name="infractiontype"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_infraction_guild_id", "infraction", ["guild_id"])
    op.create_index("ix_infraction_user_id", "infraction", ["user_id"])

    op.create_table(
        "moderation_config",
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("escalation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("escalation_thresholds", sa.JSON(), nullable=False),
        sa.Column("filtered_words", sa.JSON(), nullable=False),
        sa.Column("link_filter_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("link_filter_config", sa.JSON(), nullable=False),
        sa.Column("spam_filter_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("spam_filter_config", sa.JSON(), nullable=False),
        sa.Column("caps_filter_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("caps_filter_config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "custom_command",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=80), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("embed_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("embed_config", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("restriction_type", sa.String(length=20), nullable=False, server_default="public"),
        sa.Column("restricted_role_id", sa.BigInteger(), nullable=True),
        sa.Column("restricted_permission", sa.String(length=64), nullable=True),
        sa.Column("cooldown_type", sa.String(length=10), nullable=False, server_default="none"),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_logging_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("guild_id", "trigger", name="uq_custom_command_guild_trigger"),
    )
    op.create_index("ix_custom_command_guild_id", "custom_command", ["guild_id"])

    op.create_table(
        "music_config",
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            sa.ForeignKey("guild_config.guild_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_volume", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("max_queue_size", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("dj_role_id", sa.BigInteger(), nullable=True),
        sa.Column("music_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("music_config")
    op.drop_index("ix_custom_command_guild_id", table_name="custom_command")
    op.drop_table("custom_command")
    op.drop_table("moderation_config")
    op.drop_index("ix_infraction_user_id", table_name="infraction")
    op.drop_index("ix_infraction_guild_id", table_name="infraction")
    op.drop_table("infraction")
    op.drop_index("ix_role_binding_source_message_id", table_name="role_binding")
    op.drop_index("ix_role_binding_guild_id", table_name="role_binding")
    op.drop_table("role_binding")
    op.drop_table("welcome_config")
    op.drop_table("guild_config")
    sa.Enum(name="rolebindingtype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="infractiontype").drop(op.get_bind(), checkfirst=True)
