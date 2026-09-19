from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class BotGuild(Base, TimestampMixin):
    """Tracks which guilds the bot is currently a member of.

    Deliberately independent of `guild_config`: that table is created
    lazily on first `/config` use, so it can't answer "is the bot actually
    in this guild right now" for a guild nobody has configured yet (e.g.
    one the bot was just invited to). This table exists purely so other
    processes - namely the web dashboard, which has no Discord gateway
    connection of its own - have an authoritative source for that question.
    """

    __tablename__ = "bot_guild"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return f"BotGuild(guild_id={self.guild_id}, is_present={self.is_present})"
