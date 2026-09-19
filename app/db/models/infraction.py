from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class InfractionType(enum.StrEnum):
    WARN = "warn"
    TIMEOUT = "timeout"
    KICK = "kick"
    BAN = "ban"


class Infraction(Base, GuildScopedMixin, TimestampMixin):
    __tablename__ = "infraction"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    moderator_id: Mapped[int] = mapped_column(BigInteger)

    type: Mapped[InfractionType] = mapped_column(Enum(InfractionType))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Duration in seconds, when applicable (e.g. timeouts).
    duration_seconds: Mapped[int | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return (
            f"Infraction(id={self.id}, guild_id={self.guild_id}, "
            f"user_id={self.user_id}, type={self.type})"
        )

