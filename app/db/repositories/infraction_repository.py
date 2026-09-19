from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.sql import Select

from app.db.models.infraction import Infraction, InfractionType
from app.db.repositories.base import BaseRepository

_SORT_COLUMNS = {
    "created_at": Infraction.created_at,
    "type": Infraction.type,
    "active": Infraction.active,
}


def _apply_search_filters(
    stmt: Select, *, text: str | None, type: InfractionType | None, active: bool | None
) -> Select:
    if type is not None:
        stmt = stmt.where(Infraction.type == type)
    if active is not None:
        stmt = stmt.where(Infraction.active.is_(active))
    if text:
        pattern = f"%{text}%"
        stmt = stmt.where(
            or_(
                Infraction.reason.ilike(pattern),
                cast(Infraction.user_id, String).like(pattern),
                cast(Infraction.moderator_id, String).like(pattern),
            )
        )
    return stmt


class InfractionRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        *,
        user_id: int,
        moderator_id: int,
        type: InfractionType,
        reason: str | None = None,
        duration_seconds: int | None = None,
        expires_at: datetime | None = None,
    ) -> Infraction:
        infraction = Infraction(
            guild_id=guild_id,
            user_id=user_id,
            moderator_id=moderator_id,
            type=type,
            reason=reason,
            duration_seconds=duration_seconds,
            expires_at=expires_at,
        )
        self.session.add(infraction)
        await self.session.flush()
        return infraction

    async def get(self, guild_id: int, infraction_id: int) -> Infraction | None:
        stmt = select(Infraction).where(
            Infraction.id == infraction_id, Infraction.guild_id == guild_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, guild_id: int, user_id: int) -> list[Infraction]:
        # id.desc() as a tiebreaker: created_at alone isn't reliable when two
        # infractions land in the same timestamp-resolution window (e.g. an
        # auto-escalation recorded immediately after the warning that
        # triggered it); autoincrement id is monotonic with insertion order.
        stmt = (
            select(Infraction)
            .where(Infraction.guild_id == guild_id, Infraction.user_id == user_id)
            .order_by(Infraction.created_at.desc(), Infraction.id.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_for_guild(
        self, guild_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[Infraction]:
        stmt = (
            select(Infraction)
            .where(Infraction.guild_id == guild_id)
            .order_by(Infraction.created_at.desc(), Infraction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def search_for_guild(
        self,
        guild_id: int,
        *,
        text: str | None = None,
        type: InfractionType | None = None,
        active: bool | None = None,
        sort: str = "created_at",
        direction: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Infraction]:
        """Like list_for_guild, but with optional text/type/active filters
        and a sort column - `text` matches against reason (case-insensitive)
        or a raw user_id/moderator_id substring, since display names are
        never persisted (only resolved live from Discord per page-load, see
        the moderation router) so there's nothing to search by name against.
        `sort` falls back to created_at for anything not in _SORT_COLUMNS -
        a bad/forged query param degrades to the default order rather than
        erroring."""
        column = _SORT_COLUMNS.get(sort, Infraction.created_at)
        order = column.asc() if direction == "asc" else column.desc()
        stmt = select(Infraction).where(Infraction.guild_id == guild_id)
        stmt = _apply_search_filters(stmt, text=text, type=type, active=active)
        stmt = stmt.order_by(order, Infraction.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_search_for_guild(
        self, guild_id: int, *, text: str | None = None, type: InfractionType | None = None, active: bool | None = None
    ) -> int:
        stmt = select(func.count()).where(Infraction.guild_id == guild_id)
        stmt = _apply_search_filters(stmt, text=text, type=type, active=active)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_active_for_user(self, guild_id: int, user_id: int) -> list[Infraction]:
        stmt = (
            select(Infraction)
            .where(
                Infraction.guild_id == guild_id,
                Infraction.user_id == user_id,
                Infraction.active.is_(True),
            )
            .order_by(Infraction.created_at.desc(), Infraction.id.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_for_guild(self, guild_id: int) -> int:
        stmt = select(func.count()).where(Infraction.guild_id == guild_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_active_by_type(self, guild_id: int, user_id: int, type: InfractionType) -> int:
        stmt = select(func.count()).where(
            Infraction.guild_id == guild_id,
            Infraction.user_id == user_id,
            Infraction.type == type,
            Infraction.active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def set_active(self, guild_id: int, infraction_id: int, active: bool) -> Infraction | None:
        infraction = await self.get(guild_id, infraction_id)
        if infraction is None:
            return None
        infraction.active = active
        await self.session.flush()
        return infraction

    async def update_reason(self, guild_id: int, infraction_id: int, reason: str | None) -> Infraction | None:
        infraction = await self.get(guild_id, infraction_id)
        if infraction is None:
            return None
        infraction.reason = reason
        await self.session.flush()
        return infraction

    async def delete(self, guild_id: int, infraction_id: int) -> bool:
        infraction = await self.get(guild_id, infraction_id)
        if infraction is None:
            return False
        await self.session.delete(infraction)
        await self.session.flush()
        return True

    async def resolve_active_by_type(self, guild_id: int, user_id: int, type: InfractionType) -> int:
        """Mark every active infraction of `type` for this user as resolved. Returns the count."""
        stmt = select(Infraction).where(
            Infraction.guild_id == guild_id,
            Infraction.user_id == user_id,
            Infraction.type == type,
            Infraction.active.is_(True),
        )
        result = await self.session.execute(stmt)
        infractions = list(result.scalars())
        for infraction in infractions:
            infraction.active = False
        await self.session.flush()
        return len(infractions)
