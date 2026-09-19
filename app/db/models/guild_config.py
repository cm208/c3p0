from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class GuildConfig(Base, GuildScopedMixin, TimestampMixin):
    """Core per-guild settings.

    One row per guild the bot has ever configured. Created lazily (on first
    admin interaction, or on guild join) rather than requiring an explicit
    setup command.
    """

    __tablename__ = "guild_config"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prefix: Mapped[str] = mapped_column(String(10), default="!")
    default_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    log_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    moderation_log_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:
        return f"GuildConfig(guild_id={self.guild_id}, prefix={self.prefix!r})"
