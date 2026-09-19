from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class MusicConfig(Base, GuildScopedMixin, TimestampMixin):
    """Persistent per-guild music settings.

    Transient playback state (current track, queue contents) is NOT stored
    here - it lives in an in-memory GuildPlayer per architecture.md.
    """

    __tablename__ = "music_config"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), primary_key=True
    )

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_volume: Mapped[int] = mapped_column(Integer, default=50)
    max_queue_size: Mapped[int] = mapped_column(Integer, default=100)
    dj_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    music_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:
        return f"MusicConfig(guild_id={self.guild_id}, enabled={self.enabled})"
