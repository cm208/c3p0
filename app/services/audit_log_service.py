"""Audit log for dashboard-driven role/channel create/edit/delete.

Plain Python in/out, no discord.py types - every server-management route
(and template-apply, per item) calls record() after a successful Discord
call. This is a required part of the server-management feature, not an
optional add-on - every role/channel create/edit/delete must be traceable
to who did it and when.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.audit_log_entry import AuditAction, AuditLogEntry, AuditTargetType
from app.db.repositories.audit_log_repository import AuditLogRepository
from app.db.repositories.guild_config_repository import GuildConfigRepository


@dataclass(frozen=True, slots=True)
class AuditLogEntryView:
    id: int
    guild_id: int
    actor_discord_user_id: int
    action: AuditAction
    target_type: AuditTargetType
    target_id: int
    target_name: str
    summary: str
    created_at: datetime


def _to_view(entry: AuditLogEntry) -> AuditLogEntryView:
    return AuditLogEntryView(
        id=entry.id,
        guild_id=entry.guild_id,
        actor_discord_user_id=entry.actor_discord_user_id,
        action=entry.action,
        target_type=entry.target_type,
        target_id=entry.target_id,
        target_name=entry.target_name,
        summary=entry.summary,
        created_at=entry.created_at,
    )


class AuditLogService:
    def __init__(self, default_prefix: str = "!") -> None:
        # Only needed so record() still creates a GuildConfig row with the
        # right default prefix when it's the very first write for a guild
        # (see _ensure_guild_row), rather than silently hardcoding "!".
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # AuditLogEntry.guild_id FKs to guild_config.guild_id, enforced
        # against the real sqlite file (the in-memory test db doesn't
        # enforce FKs, so a missing call here wouldn't fail any test).
        # record() can be the very first DB write ever made for a guild -
        # an admin's first action after inviting the bot could be creating
        # a role from the dashboard, with no guild_config row yet.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def record(
        self,
        guild_id: int,
        *,
        actor_discord_user_id: int,
        action: AuditAction,
        target_type: AuditTargetType,
        target_id: int,
        target_name: str,
        summary: str,
    ) -> AuditLogEntryView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            entry = await AuditLogRepository(session).create(
                guild_id,
                actor_discord_user_id=actor_discord_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                summary=summary,
            )
            return _to_view(entry)

    async def list_for_guild(
        self, guild_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[AuditLogEntryView]:
        async with session_scope() as session:
            entries = await AuditLogRepository(session).list_for_guild(
                guild_id, limit=limit, offset=offset
            )
            return [_to_view(e) for e in entries]

    async def count_for_guild(self, guild_id: int) -> int:
        async with session_scope() as session:
            return await AuditLogRepository(session).count_for_guild(guild_id)
