from __future__ import annotations

from sqlalchemy import JSON, BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class GuildTemplate(Base, TimestampMixin):
    """A named, reusable bundle of role/channel definitions.

    Deliberately NOT GuildScopedMixin: guild_id is nullable by design. A
    null guild_id means a built-in starter template - visible to every
    guild - and GuildScopedMixin's whole contract is "always filtered by
    guild_id", which is false here. Follows BotGuild's precedent
    (Base, TimestampMixin only) instead. In practice the built-in catalog
    itself lives in app/services/builtin_templates.py as plain Python data,
    never as a row here - only a guild's own saved templates get inserted,
    so guild_id is always set on real rows today. The column stays nullable
    to match that design.
    """

    __tablename__ = "guild_template"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    guild_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Structured role/channel/permission-overwrite data - see
    # app/services/builtin_templates.py's TemplateDefinition for the shape.
    definition: Mapped[dict] = mapped_column(JSON)

    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:
        return f"GuildTemplate(id={self.id}, guild_id={self.guild_id}, name={self.name!r})"
