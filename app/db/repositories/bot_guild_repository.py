from __future__ import annotations

from sqlalchemy import select

from app.db.models.bot_guild import BotGuild
from app.db.repositories.base import BaseRepository


class BotGuildRepository(BaseRepository):
    async def mark_present(self, guild_id: int, name: str | None) -> BotGuild:
        record = await self.session.get(BotGuild, guild_id)
        if record is None:
            record = BotGuild(guild_id=guild_id, name=name, is_present=True)
            self.session.add(record)
        else:
            record.name = name
            record.is_present = True
        await self.session.flush()
        return record

    async def mark_absent(self, guild_id: int) -> None:
        record = await self.session.get(BotGuild, guild_id)
        if record is None:
            return
        record.is_present = False
        await self.session.flush()

    async def is_present(self, guild_id: int) -> bool:
        record = await self.session.get(BotGuild, guild_id)
        return record is not None and record.is_present

    async def get_name(self, guild_id: int) -> str | None:
        record = await self.session.get(BotGuild, guild_id)
        return record.name if record is not None else None

    async def present_guild_ids(self) -> list[int]:
        result = await self.session.execute(
            select(BotGuild.guild_id).where(BotGuild.is_present.is_(True))
        )
        return [row[0] for row in result.all()]

    async def present_guilds(self) -> dict[int, str | None]:
        """guild_id -> name, for every guild currently marked present.

        Used by the web dashboard's guild picker so it can show a real
        server name without needing its own Discord API call - the bot
        already has this from on_ready/on_guild_join.
        """
        result = await self.session.execute(
            select(BotGuild.guild_id, BotGuild.name).where(BotGuild.is_present.is_(True))
        )
        return dict(result.all())

    async def reconcile(self, current: dict[int, str]) -> None:
        """Bring stored membership in line with `current` (guild_id -> name).

        Marks every guild in `current` present (creating rows as needed) and
        flips anything previously marked present but absent from `current`
        to absent - this is what catches a removal that happened while the
        bot was offline, which on_guild_remove never fires for.
        """
        for guild_id, name in current.items():
            await self.mark_present(guild_id, name)

        stale_ids = set(await self.present_guild_ids()) - set(current)
        for guild_id in stale_ids:
            await self.mark_absent(guild_id)
