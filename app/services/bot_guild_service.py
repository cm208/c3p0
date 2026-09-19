"""Bot guild-membership service.

Plain Python in/out, like every other service - the web dashboard (which
has no Discord gateway connection) is as much a caller of this as the bot
itself is, and that's the whole point of it existing.
"""

from __future__ import annotations

from app.db.database import session_scope
from app.db.repositories.bot_guild_repository import BotGuildRepository


class BotGuildService:
    async def mark_present(self, guild_id: int, name: str | None) -> None:
        async with session_scope() as session:
            await BotGuildRepository(session).mark_present(guild_id, name)

    async def mark_absent(self, guild_id: int) -> None:
        async with session_scope() as session:
            await BotGuildRepository(session).mark_absent(guild_id)

    async def is_present(self, guild_id: int) -> bool:
        async with session_scope() as session:
            return await BotGuildRepository(session).is_present(guild_id)

    async def get_name(self, guild_id: int) -> str | None:
        async with session_scope() as session:
            return await BotGuildRepository(session).get_name(guild_id)

    async def present_guild_ids(self) -> list[int]:
        async with session_scope() as session:
            return await BotGuildRepository(session).present_guild_ids()

    async def present_guilds(self) -> dict[int, str | None]:
        async with session_scope() as session:
            return await BotGuildRepository(session).present_guilds()

    async def reconcile(self, current: dict[int, str]) -> None:
        async with session_scope() as session:
            await BotGuildRepository(session).reconcile(current)
