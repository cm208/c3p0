from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class WelcomeConfig(Base, GuildScopedMixin, TimestampMixin):
    __tablename__ = "welcome_config"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), primary_key=True
    )

    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    message_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    embed_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    embed_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    embed_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embed_footer: Mapped[str | None] = mapped_column(String(256), nullable=True)

    dm_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dm_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    role_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    join_log_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"WelcomeConfig(guild_id={self.guild_id}, enabled={self.enabled})"
