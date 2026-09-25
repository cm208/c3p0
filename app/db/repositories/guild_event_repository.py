from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select

from app.db.models.guild_event import GuildEvent
from app.db.repositories.base import BaseRepository


class GuildEventRepository(BaseRepository):
    async def create(self, guild_id: int, *, tag: str, text: str) -> GuildEvent:
        event = GuildEvent(guild_id=guild_id, tag=tag, text=text)
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_after(self, guild_id: int, *, after_id: int, limit: int) -> list[GuildEvent]:
        """Events newer than `after_id`, oldest first (the order a feed
        appends them in). With after_id=0 this is the newest `limit` events."""
        stmt = (
            select(GuildEvent)
            .where(GuildEvent.guild_id == guild_id, GuildEvent.id > after_id)
            .order_by(GuildEvent.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(reversed(list(result.scalars())))

    async def count_since(self, guild_id: int, *, tag: str, since: datetime) -> int:
        stmt = select(func.count()).where(
            GuildEvent.guild_id == guild_id,
            GuildEvent.tag == tag,
            GuildEvent.created_at >= since,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def prune(self, guild_id: int, *, keep: int) -> None:
        """Delete all but the newest `keep` events for guild_id."""
        cutoff_stmt = (
            select(GuildEvent.id)
            .where(GuildEvent.guild_id == guild_id)
            .order_by(GuildEvent.id.desc())
            .offset(keep)
            .limit(1)
        )
        cutoff = (await self.session.execute(cutoff_stmt)).scalar_one_or_none()
        if cutoff is None:
            return
        await self.session.execute(
            delete(GuildEvent).where(GuildEvent.guild_id == guild_id, GuildEvent.id <= cutoff)
        )
