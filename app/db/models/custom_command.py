from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class CustomCommand(Base, GuildScopedMixin, TimestampMixin):
    """A data-driven custom command template.

    IMPORTANT: `response` is plain-text/template data, never code. It is
    rendered through an explicit, fixed variable substitution map only. It
    must never be passed to eval/exec, a shell, SQL, an HTTP client, or an
    unrestricted template engine.
    """

    __tablename__ = "custom_command"
    __table_args__ = (UniqueConstraint("guild_id", "trigger", name="uq_custom_command_guild_trigger"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(64))
    # Trigger includes the prefix as it was configured at creation time,
    # e.g. "!rules". Uniqueness is enforced per-guild.
    trigger: Mapped[str] = mapped_column(String(80))

    response: Mapped[str] = mapped_column(Text)

    embed_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    embed_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # "public", "role", or "permission" - interpreted by CustomCommandService.
    restriction_type: Mapped[str] = mapped_column(String(20), default="public")
    restricted_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    restricted_permission: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # "none", "user", or "guild".
    cooldown_type: Mapped[str] = mapped_column(String(10), default="none")
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0)

    usage_logging_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Lifetime invocation count, shown on the dashboard. Counts every
    # successful response regardless of usage_logging_enabled (which only
    # controls the per-use log line).
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_by: Mapped[int] = mapped_column(BigInteger)

    def __repr__(self) -> str:
        return f"CustomCommand(guild_id={self.guild_id}, trigger={self.trigger!r})"
