from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin

# Example shape stored in `escalation_thresholds`:
#   {"3": "timeout", "5": "kick", "7": "ban"}
# Keys are warning counts (as strings, since JSON object keys must be
# strings), values are one of InfractionType. Interpreted and validated by
# ModerationService, not enforced at the database layer.
DEFAULT_ESCALATION_THRESHOLDS: dict[str, str] = {}

# Example shape stored in `filtered_words` / `link_filter_config` /
# `spam_config` / `caps_config`: small JSON documents owned entirely by the
# moderation service. Kept as JSON blobs here rather than normalized tables
# since they're always read/written as a whole per guild.
DEFAULT_FILTER_CONFIG: dict = {}


class ModerationConfig(Base, GuildScopedMixin, TimestampMixin):
    __tablename__ = "moderation_config"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), primary_key=True
    )

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    escalation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_thresholds: Mapped[dict] = mapped_column(
        JSON, default=lambda: dict(DEFAULT_ESCALATION_THRESHOLDS)
    )

    filtered_words: Mapped[list] = mapped_column(JSON, default=list)

    link_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    link_filter_config: Mapped[dict] = mapped_column(JSON, default=dict)

    spam_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    spam_filter_config: Mapped[dict] = mapped_column(JSON, default=dict)

    caps_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    caps_filter_config: Mapped[dict] = mapped_column(JSON, default=dict)

    def __repr__(self) -> str:
        return f"ModerationConfig(guild_id={self.guild_id}, enabled={self.enabled})"
