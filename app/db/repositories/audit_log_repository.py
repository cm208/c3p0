from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.audit_log_entry import AuditAction, AuditLogEntry, AuditTargetType
from app.db.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        *,
        actor_discord_user_id: int,
        action: AuditAction,
        target_type: AuditTargetType,
        target_id: int,
        target_name: str,
        summary: str,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            guild_id=guild_id,
            actor_discord_user_id=actor_discord_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            summary=summary,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_guild(
        self, guild_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[AuditLogEntry]:
        # id.desc() tiebreak, same reasoning as InfractionRepository: two
        # entries can land in the same timestamp-resolution window (e.g. a
        # template apply creating several roles back to back).
        stmt = (
            select(AuditLogEntry)
            .where(AuditLogEntry.guild_id == guild_id)
            .order_by(AuditLogEntry.created_at.desc(), AuditLogEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_for_guild(self, guild_id: int) -> int:
        stmt = select(func.count()).where(AuditLogEntry.guild_id == guild_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()
