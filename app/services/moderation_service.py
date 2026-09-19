"""Moderation service - infraction persistence and escalation decisions.

Like the other services, this takes/returns plain Python - no discord.py
types. Actually performing the Discord-side action (kick/ban/timeout) is
the cog's job: it checks hierarchy via app/utils/permissions.py, calls the
relevant discord.py method, and only then records the outcome here. This
keeps the escalation decision testable without any discord.py fakes at
all - see check_escalation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.infraction import Infraction, InfractionType
from app.db.models.moderation_config import ModerationConfig
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.infraction_repository import InfractionRepository
from app.db.repositories.moderation_config_repository import ModerationConfigRepository

MAX_REASON_LENGTH = 512  # Discord's own audit-log reason cap
MAX_TIMEOUT_SECONDS = 28 * 24 * 3600  # Discord's hard cap on timeout duration
MAX_CLEAR_AMOUNT = 100  # Discord's bulk-delete cap

_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

_ESCALATION_ACTIONS = {InfractionType.TIMEOUT, InfractionType.KICK, InfractionType.BAN}


class ModerationValidationError(Exception):
    """Raised for invalid moderation input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class InfractionView:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int
    type: InfractionType
    reason: str | None
    duration_seconds: int | None
    expires_at: datetime | None
    active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ModerationConfigView:
    guild_id: int
    escalation_enabled: bool
    escalation_thresholds: dict[str, str]


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    action: InfractionType | None
    warning_count: int


def _infraction_to_view(infraction: Infraction) -> InfractionView:
    return InfractionView(
        id=infraction.id,
        guild_id=infraction.guild_id,
        user_id=infraction.user_id,
        moderator_id=infraction.moderator_id,
        type=infraction.type,
        reason=infraction.reason,
        duration_seconds=infraction.duration_seconds,
        expires_at=infraction.expires_at,
        active=infraction.active,
        created_at=infraction.created_at,
    )


def _config_to_view(config: ModerationConfig) -> ModerationConfigView:
    return ModerationConfigView(
        guild_id=config.guild_id,
        escalation_enabled=config.escalation_enabled,
        escalation_thresholds=dict(config.escalation_thresholds),
    )


def validate_reason(reason: str | None) -> str | None:
    """Trim and length-check a moderation reason. `None` means "no reason given"."""
    if reason is None:
        return None
    reason = reason.strip()
    if not reason:
        return None
    if len(reason) > MAX_REASON_LENGTH:
        raise ModerationValidationError(f"Reason must be {MAX_REASON_LENGTH} characters or fewer.")
    return reason


def parse_duration(text: str) -> int:
    """Parse a duration like `10m`, `1h`, `1h30m`, `2d` into whole seconds."""
    text = text.strip()
    if not text:
        raise ModerationValidationError("Duration cannot be empty.")

    matches = _DURATION_RE.findall(text)
    leftover = _DURATION_RE.sub("", text).strip()
    if not matches or leftover:
        raise ModerationValidationError(
            f"{text!r} isn't a valid duration. Use formats like 10m, 1h, 2d, 1h30m."
        )

    total = sum(int(amount) * _UNIT_SECONDS[unit.lower()] for amount, unit in matches)
    if total <= 0:
        raise ModerationValidationError("Duration must be greater than zero.")
    if total > MAX_TIMEOUT_SECONDS:
        raise ModerationValidationError("Timeouts can't be longer than 28 days.")
    return total


def validate_clear_amount(amount: int) -> int:
    if amount < 1:
        raise ModerationValidationError("Amount must be at least 1.")
    if amount > MAX_CLEAR_AMOUNT:
        raise ModerationValidationError(f"Amount can't be more than {MAX_CLEAR_AMOUNT}.")
    return amount


class ModerationService:
    def __init__(self, default_prefix: str = "!") -> None:
        # Only needed so that configuring escalation before ever running
        # /config still creates a GuildConfig row with the right default
        # prefix, rather than silently hardcoding "!" - see _ensure_guild_row.
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # ModerationConfig.guild_id FKs to guild_config.guild_id, enforced
        # (PRAGMA foreign_keys=ON) against the real sqlite file this runs
        # against in production - unlike the in-memory test database, which
        # silently ignores FKs entirely. A guild the bot just joined has no
        # guild_config row yet (on_guild_join only touches bot_guild), so
        # get_or_create() below would otherwise raise an IntegrityError the
        # first time escalation config is touched for that guild.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def record_infraction(
        self,
        guild_id: int,
        *,
        user_id: int,
        moderator_id: int,
        type: InfractionType,
        reason: str | None = None,
        duration_seconds: int | None = None,
    ) -> InfractionView:
        reason = validate_reason(reason)
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=duration_seconds)
            if type == InfractionType.TIMEOUT and duration_seconds
            else None
        )
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            infraction = await InfractionRepository(session).create(
                guild_id,
                user_id=user_id,
                moderator_id=moderator_id,
                type=type,
                reason=reason,
                duration_seconds=duration_seconds,
                expires_at=expires_at,
            )
            return _infraction_to_view(infraction)

    async def list_infractions(
        self, guild_id: int, user_id: int, *, active_only: bool = False
    ) -> list[InfractionView]:
        async with session_scope() as session:
            repo = InfractionRepository(session)
            infractions = (
                await repo.list_active_for_user(guild_id, user_id)
                if active_only
                else await repo.list_for_user(guild_id, user_id)
            )
            return [_infraction_to_view(i) for i in infractions]

    async def list_infractions_for_guild(
        self, guild_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[InfractionView]:
        async with session_scope() as session:
            infractions = await InfractionRepository(session).list_for_guild(
                guild_id, limit=limit, offset=offset
            )
            return [_infraction_to_view(i) for i in infractions]

    async def count_infractions_for_guild(self, guild_id: int) -> int:
        async with session_scope() as session:
            return await InfractionRepository(session).count_for_guild(guild_id)

    async def resolve_infraction(self, guild_id: int, infraction_id: int) -> InfractionView | None:
        async with session_scope() as session:
            infraction = await InfractionRepository(session).set_active(guild_id, infraction_id, False)
            return _infraction_to_view(infraction) if infraction is not None else None

    async def get_infraction(self, guild_id: int, infraction_id: int) -> InfractionView | None:
        async with session_scope() as session:
            infraction = await InfractionRepository(session).get(guild_id, infraction_id)
            return _infraction_to_view(infraction) if infraction is not None else None

    async def update_infraction_reason(
        self, guild_id: int, infraction_id: int, reason: str | None
    ) -> InfractionView | None:
        reason = validate_reason(reason)
        async with session_scope() as session:
            infraction = await InfractionRepository(session).update_reason(guild_id, infraction_id, reason)
            return _infraction_to_view(infraction) if infraction is not None else None

    async def delete_infraction(self, guild_id: int, infraction_id: int) -> bool:
        async with session_scope() as session:
            return await InfractionRepository(session).delete(guild_id, infraction_id)

    async def search_infractions_for_guild(
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
    ) -> list[InfractionView]:
        async with session_scope() as session:
            infractions = await InfractionRepository(session).search_for_guild(
                guild_id, text=text, type=type, active=active, sort=sort, direction=direction,
                limit=limit, offset=offset,
            )
            return [_infraction_to_view(i) for i in infractions]

    async def count_search_infractions_for_guild(
        self, guild_id: int, *, text: str | None = None, type: InfractionType | None = None, active: bool | None = None
    ) -> int:
        async with session_scope() as session:
            return await InfractionRepository(session).count_search_for_guild(
                guild_id, text=text, type=type, active=active
            )

    async def resolve_active_bans(self, guild_id: int, user_id: int) -> int:
        """Mark active BAN infractions resolved - called after a successful !unban."""
        async with session_scope() as session:
            return await InfractionRepository(session).resolve_active_by_type(
                guild_id, user_id, InfractionType.BAN
            )

    async def get_config(self, guild_id: int) -> ModerationConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await ModerationConfigRepository(session).get_or_create(guild_id)
            return _config_to_view(config)

    async def set_escalation_enabled(self, guild_id: int, enabled: bool) -> ModerationConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await ModerationConfigRepository(session).set_escalation_enabled(guild_id, enabled)
            return _config_to_view(config)

    async def set_escalation_threshold(
        self, guild_id: int, warnings: int, action: InfractionType
    ) -> ModerationConfigView:
        if warnings < 1:
            raise ModerationValidationError("Warning count must be at least 1.")
        if action not in _ESCALATION_ACTIONS:
            raise ModerationValidationError("Escalation action must be timeout, kick, or ban.")
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            repo = ModerationConfigRepository(session)
            config = await repo.get_or_create(guild_id)
            thresholds = dict(config.escalation_thresholds)
            thresholds[str(warnings)] = action.value
            updated = await repo.set_escalation_thresholds(guild_id, thresholds)
            return _config_to_view(updated)

    async def clear_escalation_threshold(self, guild_id: int, warnings: int) -> ModerationConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            repo = ModerationConfigRepository(session)
            config = await repo.get_or_create(guild_id)
            thresholds = dict(config.escalation_thresholds)
            thresholds.pop(str(warnings), None)
            updated = await repo.set_escalation_thresholds(guild_id, thresholds)
            return _config_to_view(updated)

    async def check_escalation(self, guild_id: int, user_id: int) -> EscalationDecision:
        """Decide whether a warning count has just crossed a configured threshold.

        Only fires on an exact match (warning count == a configured threshold
        key) so a single crossing triggers exactly once, rather than
        re-triggering on every subsequent warning past that count.
        """
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await ModerationConfigRepository(session).get_or_create(guild_id)
            count = await InfractionRepository(session).count_active_by_type(
                guild_id, user_id, InfractionType.WARN
            )

        if not config.escalation_enabled:
            return EscalationDecision(action=None, warning_count=count)

        action_name = config.escalation_thresholds.get(str(count))
        action = InfractionType(action_name) if action_name else None
        return EscalationDecision(action=action, warning_count=count)
