"""Per-guild live activity feed - the dashboard's SYSLOG column.

The bot process records short lines as things happen (see the cogs'
_record_event calls); the web process polls list_after() via
GET /guilds/{id}/events. Plain Python in/out, no discord.py types.

record() never raises. The feed is telemetry: a failed write must not
turn a successful member join, command response or moderation action
into an error for the user. Failures are logged and dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.guild_event import GuildEvent
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.guild_event_repository import GuildEventRepository

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 300
# Rows kept per guild. Enough for a week of joins on a busy server (the
# Server page's "joined this week" tile counts from here) while keeping the
# table small; pruning runs on every PRUNE_EVERY-th write, not every write.
KEEP_PER_GUILD = 2000
PRUNE_EVERY = 50
MAX_LIST_LIMIT = 100


class EventTag:
    JOIN = "JOIN"
    LEAVE = "LEAVE"
    CMD = "CMD"
    MOD = "MOD"
    MUSIC = "MUSIC"


@dataclass(frozen=True, slots=True)
class GuildEventView:
    id: int
    tag: str
    text: str
    created_at: datetime


def _to_view(event: GuildEvent) -> GuildEventView:
    return GuildEventView(id=event.id, tag=event.tag, text=event.text, created_at=event.created_at)


class EventLogService:
    def __init__(self, default_prefix: str = "!") -> None:
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # GuildEvent.guild_id FKs to guild_config.guild_id. A member joining
        # a server the bot was just invited to can be the very first write.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def record(self, guild_id: int, tag: str, text: str) -> None:
        text = " ".join(text.split())[:MAX_TEXT_LENGTH]
        try:
            async with session_scope() as session:
                await self._ensure_guild_row(session, guild_id)
                repo = GuildEventRepository(session)
                event = await repo.create(guild_id, tag=tag, text=text)
                if event.id % PRUNE_EVERY == 0:
                    await repo.prune(guild_id, keep=KEEP_PER_GUILD)
        except Exception:
            logger.exception("Failed to record guild event", extra={"guild_id": guild_id, "tag": tag})

    async def list_after(self, guild_id: int, *, after_id: int = 0, limit: int = 40) -> list[GuildEventView]:
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        async with session_scope() as session:
            events = await GuildEventRepository(session).list_after(guild_id, after_id=after_id, limit=limit)
            return [_to_view(e) for e in events]

    async def net_joins_since(self, guild_id: int, *, days: int = 7) -> int:
        since = datetime.now(UTC) - timedelta(days=days)
        async with session_scope() as session:
            repo = GuildEventRepository(session)
            joins = await repo.count_since(guild_id, tag=EventTag.JOIN, since=since)
            leaves = await repo.count_since(guild_id, tag=EventTag.LEAVE, since=since)
            return joins - leaves
