"""Custom command service - creation/config, safe rendering, restriction and cooldown checks.

Like the other services, plain Python in/out - no discord.py types.
Restriction checks (role/permission) take plain sets of role IDs / granted
permission names rather than a discord.Member, and cooldown tracking is a
small in-memory dict owned by this service instance - transient cooldown
timers deliberately aren't persisted unless necessary, so a restart simply
resets them, which is fine.

Response rendering reuses app/utils/templates.py verbatim - the same
mechanism and the same fixed variable set app/services/welcome_service.py
uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.custom_command import CustomCommand
from app.db.repositories.custom_command_repository import CustomCommandRepository
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.utils.templates import (
    STANDARD_VARIABLES,
    TemplateContext,
    UnknownTemplateVariableError,
    render_template_context,
    validate_template,
)

MAX_NAME_LENGTH = 64
MAX_TRIGGER_LENGTH = 80
MAX_RESPONSE_LENGTH = 2000  # Discord message content limit

RESTRICTION_TYPES = {"public", "role", "permission"}
COOLDOWN_TYPES = {"none", "user", "guild"}


class CustomCommandValidationError(Exception):
    """Raised for invalid custom-command input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class CustomCommandView:
    id: int
    guild_id: int
    name: str
    trigger: str
    response: str
    embed_enabled: bool
    enabled: bool
    restriction_type: str
    restricted_role_id: int | None
    restricted_permission: str | None
    cooldown_type: str
    cooldown_seconds: int
    usage_logging_enabled: bool
    created_by: int
    use_count: int = 0


def _to_view(command: CustomCommand) -> CustomCommandView:
    return CustomCommandView(
        id=command.id,
        guild_id=command.guild_id,
        name=command.name,
        trigger=command.trigger,
        response=command.response,
        embed_enabled=command.embed_enabled,
        enabled=command.enabled,
        restriction_type=command.restriction_type,
        restricted_role_id=command.restricted_role_id,
        restricted_permission=command.restricted_permission,
        cooldown_type=command.cooldown_type,
        cooldown_seconds=command.cooldown_seconds,
        usage_logging_enabled=command.usage_logging_enabled,
        created_by=command.created_by,
        use_count=command.use_count or 0,
    )


def _validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise CustomCommandValidationError("Name cannot be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise CustomCommandValidationError(f"Name must be {MAX_NAME_LENGTH} characters or fewer.")
    return name


def _validate_trigger(trigger: str) -> str:
    trigger = trigger.strip()
    if not trigger:
        raise CustomCommandValidationError("Trigger cannot be empty.")
    if " " in trigger:
        raise CustomCommandValidationError("Trigger cannot contain spaces.")
    if len(trigger) > MAX_TRIGGER_LENGTH:
        raise CustomCommandValidationError(f"Trigger must be {MAX_TRIGGER_LENGTH} characters or fewer.")
    return trigger


def _validate_response(response: str) -> str:
    response = response.strip()
    if not response:
        raise CustomCommandValidationError("Response cannot be empty.")
    if len(response) > MAX_RESPONSE_LENGTH:
        raise CustomCommandValidationError(f"Response must be {MAX_RESPONSE_LENGTH} characters or fewer.")
    try:
        validate_template(response, STANDARD_VARIABLES)
    except UnknownTemplateVariableError as exc:
        raise CustomCommandValidationError(str(exc)) from exc
    return response


class CustomCommandService:
    def __init__(self, default_prefix: str = "!") -> None:
        # (command_id, user_id | None) -> last-used timestamp. None user_id
        # means a per-guild cooldown (shared across all users).
        self._cooldowns: dict[tuple[int, int | None], datetime] = {}
        # Only needed so that creating a command before ever running /config
        # still creates a GuildConfig row with the right default prefix,
        # rather than silently hardcoding "!" - see _ensure_guild_row.
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # CustomCommand.guild_id FKs to guild_config.guild_id, enforced
        # (PRAGMA foreign_keys=ON) against the real sqlite file this runs
        # against in production - unlike the in-memory test database, which
        # silently ignores FKs entirely. A guild the bot just joined has no
        # guild_config row yet (on_guild_join only touches bot_guild), so
        # create() below would otherwise raise an IntegrityError the first
        # time it's called before /config or a member join ever runs for
        # that guild. Only create() needs this - every other method here
        # operates on a command that already exists, which means its parent
        # row must already exist too.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def create(
        self, guild_id: int, *, name: str, trigger: str, response: str, created_by: int
    ) -> CustomCommandView:
        name = _validate_name(name)
        trigger = _validate_trigger(trigger)
        response = _validate_response(response)

        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            repo = CustomCommandRepository(session)
            if await repo.get_by_trigger(guild_id, trigger) is not None:
                raise CustomCommandValidationError(f"A command with trigger `{trigger}` already exists.")
            command = await repo.create(
                guild_id, name=name, trigger=trigger, response=response, created_by=created_by
            )
            return _to_view(command)

    async def get_by_trigger(self, guild_id: int, trigger: str) -> CustomCommandView | None:
        async with session_scope() as session:
            command = await CustomCommandRepository(session).get_by_trigger(guild_id, trigger)
            return _to_view(command) if command is not None else None

    async def list_for_guild(self, guild_id: int) -> list[CustomCommandView]:
        async with session_scope() as session:
            commands = await CustomCommandRepository(session).list_for_guild(guild_id)
            return [_to_view(c) for c in commands]

    async def delete_by_trigger(self, guild_id: int, trigger: str) -> bool:
        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return False
            return await repo.delete(guild_id, existing.id)

    async def set_enabled_by_trigger(self, guild_id: int, trigger: str, enabled: bool) -> CustomCommandView | None:
        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return None
            command = await repo.set_enabled(guild_id, existing.id, enabled)
            return _to_view(command) if command is not None else None

    async def set_response_by_trigger(
        self, guild_id: int, trigger: str, response: str
    ) -> CustomCommandView | None:
        response = _validate_response(response)
        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return None
            command = await repo.set_response(guild_id, existing.id, response)
            return _to_view(command) if command is not None else None

    async def set_embed_enabled_by_trigger(
        self, guild_id: int, trigger: str, enabled: bool
    ) -> CustomCommandView | None:
        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return None
            command = await repo.set_embed_enabled(guild_id, existing.id, enabled)
            return _to_view(command) if command is not None else None

    async def set_restriction_by_trigger(
        self,
        guild_id: int,
        trigger: str,
        *,
        restriction_type: str,
        restricted_role_id: int | None = None,
        restricted_permission: str | None = None,
    ) -> CustomCommandView | None:
        if restriction_type not in RESTRICTION_TYPES:
            raise CustomCommandValidationError("Restriction type must be public, role, or permission.")
        if restriction_type == "role" and restricted_role_id is None:
            raise CustomCommandValidationError("A role is required for role-restricted commands.")
        if restriction_type == "permission" and not restricted_permission:
            raise CustomCommandValidationError("A permission name is required for permission-restricted commands.")

        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return None
            command = await repo.set_restriction(
                guild_id,
                existing.id,
                restriction_type=restriction_type,
                restricted_role_id=restricted_role_id if restriction_type == "role" else None,
                restricted_permission=restricted_permission if restriction_type == "permission" else None,
            )
            return _to_view(command) if command is not None else None

    async def set_cooldown_by_trigger(
        self, guild_id: int, trigger: str, *, cooldown_type: str, cooldown_seconds: int
    ) -> CustomCommandView | None:
        if cooldown_type not in COOLDOWN_TYPES:
            raise CustomCommandValidationError("Cooldown type must be none, user, or guild.")
        if cooldown_seconds < 0:
            raise CustomCommandValidationError("Cooldown seconds cannot be negative.")

        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return None
            command = await repo.set_cooldown(
                guild_id, existing.id, cooldown_type=cooldown_type, cooldown_seconds=cooldown_seconds
            )
            return _to_view(command) if command is not None else None

    async def set_usage_logging_by_trigger(
        self, guild_id: int, trigger: str, enabled: bool
    ) -> CustomCommandView | None:
        async with session_scope() as session:
            repo = CustomCommandRepository(session)
            existing = await repo.get_by_trigger(guild_id, trigger)
            if existing is None:
                return None
            command = await repo.set_usage_logging(guild_id, existing.id, enabled)
            return _to_view(command) if command is not None else None

    async def increment_use_count(self, view: CustomCommandView) -> None:
        async with session_scope() as session:
            await CustomCommandRepository(session).increment_use_count(view.guild_id, view.id)

    # --- Invocation-time helpers (no DB access - pure/in-memory) ---

    def render_response(self, view: CustomCommandView, context: TemplateContext) -> str:
        return render_template_context(view.response, context)

    def check_restriction(
        self, view: CustomCommandView, *, member_role_ids: set[int], member_permission_names: set[str]
    ) -> bool:
        """Whether a member (described by plain role IDs / granted permission names) may invoke this."""
        if view.restriction_type == "role":
            return view.restricted_role_id in member_role_ids
        if view.restriction_type == "permission":
            return view.restricted_permission in member_permission_names
        return True

    def check_cooldown(self, view: CustomCommandView, user_id: int) -> float | None:
        """Returns remaining seconds if still on cooldown, else None. Does not record usage."""
        if view.cooldown_type == "none" or view.cooldown_seconds <= 0:
            return None
        key = (view.id, user_id if view.cooldown_type == "user" else None)
        last_used = self._cooldowns.get(key)
        if last_used is None:
            return None
        elapsed = (datetime.now(UTC) - last_used).total_seconds()
        remaining = view.cooldown_seconds - elapsed
        return remaining if remaining > 0 else None

    def record_usage(self, view: CustomCommandView, user_id: int) -> None:
        if view.cooldown_type == "none":
            return
        key = (view.id, user_id if view.cooldown_type == "user" else None)
        self._cooldowns[key] = datetime.now(UTC)
