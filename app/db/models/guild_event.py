from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GuildEvent(Base, GuildScopedMixin):
    """One line of a guild's live activity feed (the dashboard's SYSLOG).

    Written by the bot process as things happen (joins, leaves, custom
    command uses, moderation actions, music changes) and polled by the web
    process. Deliberately a short rolling window, not a permanent record:
    EventLogService prunes each guild back to its newest rows on write.
    Anything that must be kept goes in AuditLogEntry or Infraction instead.
    """

    __tablename__ = "guild_event"
    __table_args__ = (Index("ix_guild_event_guild_id_id", "guild_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE")
    )
    # Short uppercase category shown as "[TAG]" - JOIN, LEAVE, CMD, MOD, MUSIC.
    tag: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:
        return f"GuildEvent(id={self.id}, guild_id={self.guild_id}, tag={self.tag!r})"
