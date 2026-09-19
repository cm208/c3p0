from __future__ import annotations

import enum

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class AuditAction(enum.StrEnum):
    ROLE_CREATE = "role_create"
    ROLE_EDIT = "role_edit"
    ROLE_DELETE = "role_delete"
    CHANNEL_CREATE = "channel_create"
    CHANNEL_EDIT = "channel_edit"
    CHANNEL_DELETE = "channel_delete"


class AuditTargetType(enum.StrEnum):
    ROLE = "role"
    CHANNEL = "channel"


class AuditLogEntry(Base, GuildScopedMixin, TimestampMixin):
    """A record of a dashboard-driven role/channel create/edit/delete.

    No dedicated TEMPLATE_APPLY action - a template-created role/channel
    gets a normal ROLE_CREATE/CHANNEL_CREATE row whose `summary` mentions
    the template by name. Keeps the action vocabulary small and every row
    independently meaningful regardless of how the item was created.
    """

    __tablename__ = "audit_log_entry"
    __table_args__ = (Index("ix_audit_log_entry_guild_id_created_at", "guild_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), index=True
    )

    actor_discord_user_id: Mapped[int] = mapped_column(BigInteger)

    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction))
    target_type: Mapped[AuditTargetType] = mapped_column(Enum(AuditTargetType))
    target_id: Mapped[int] = mapped_column(BigInteger)
    # Snapshotted at write time, not a live lookup - after a delete or
    # rename, looking the name up again by id would be wrong or impossible.
    target_name: Mapped[str] = mapped_column(String(100))

    summary: Mapped[str] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"AuditLogEntry(id={self.id}, guild_id={self.guild_id}, "
            f"action={self.action}, target={self.target_type}:{self.target_id})"
        )
