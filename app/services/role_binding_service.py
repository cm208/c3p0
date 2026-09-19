"""Self-assignable role binding service - reaction, button, and select roles.

Like the other services, this takes/returns plain Python - no discord.py
types. Emoji text is expected pre-normalized by the caller (see
app/utils/emoji.py), since normalization uses discord.PartialEmoji and
keeping that out of this module keeps it discord.py-free. Button/select
custom_id generation is likewise the cog's job (buttons) or handled here
via the binding's own row id (selects) - see create_select_binding.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.role_binding import RoleBinding, RoleBindingType
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.role_binding_repository import RoleBindingRepository


class RoleBindingValidationError(Exception):
    """Raised for invalid role-binding input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class RoleBindingView:
    id: int
    guild_id: int
    interaction_type: RoleBindingType
    source_channel_id: int
    source_message_id: int
    role_id: int
    emoji: str | None
    component_custom_id: str | None
    toggle: bool
    enabled: bool


def _to_view(binding: RoleBinding) -> RoleBindingView:
    return RoleBindingView(
        id=binding.id,
        guild_id=binding.guild_id,
        interaction_type=binding.interaction_type,
        source_channel_id=binding.source_channel_id,
        source_message_id=binding.source_message_id,
        role_id=binding.role_id,
        emoji=binding.emoji,
        component_custom_id=binding.component_custom_id,
        toggle=binding.toggle,
        enabled=binding.enabled,
    )


class RoleBindingService:
    def __init__(self, default_prefix: str = "!") -> None:
        # Only needed so that creating a binding before ever running /config
        # still creates a GuildConfig row with the right default prefix,
        # rather than silently hardcoding "!" - see _ensure_guild_row.
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # RoleBinding.guild_id FKs to guild_config.guild_id, enforced (PRAGMA
        # foreign_keys=ON) against the real sqlite file this runs against in
        # production - unlike the in-memory test database, which silently
        # ignores FKs entirely. A guild the bot just joined has no
        # guild_config row yet (on_guild_join only touches bot_guild), so
        # repo.create() below would otherwise raise an IntegrityError the
        # first time a binding is created for that guild. Only the three
        # create_*_binding methods need this - every other method here
        # operates on bindings that already exist, which means their parent
        # row must already exist too.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def create_reaction_binding(
        self,
        guild_id: int,
        *,
        channel_id: int,
        message_id: int,
        emoji: str,
        role_id: int,
        toggle: bool = True,
    ) -> RoleBindingView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            repo = RoleBindingRepository(session)
            if await repo.get_by_message_and_emoji(guild_id, message_id, emoji) is not None:
                raise RoleBindingValidationError(
                    "That emoji is already bound on this message - remove it first."
                )
            binding = await repo.create(
                guild_id,
                interaction_type=RoleBindingType.REACTION,
                source_channel_id=channel_id,
                source_message_id=message_id,
                role_id=role_id,
                emoji=emoji,
                toggle=toggle,
            )
            return _to_view(binding)

    async def find_reaction_binding(
        self, guild_id: int, message_id: int, emoji: str
    ) -> RoleBindingView | None:
        async with session_scope() as session:
            binding = await RoleBindingRepository(session).get_by_message_and_emoji(
                guild_id, message_id, emoji
            )
            return _to_view(binding) if binding is not None else None

    async def remove_reaction_binding(self, guild_id: int, message_id: int, emoji: str) -> bool:
        async with session_scope() as session:
            repo = RoleBindingRepository(session)
            existing = await repo.get_by_message_and_emoji(guild_id, message_id, emoji)
            if existing is None:
                return False
            return await repo.delete(guild_id, existing.id)

    async def create_button_binding(
        self,
        guild_id: int,
        *,
        channel_id: int,
        message_id: int,
        role_id: int,
        custom_id: str,
        emoji: str | None = None,
    ) -> RoleBindingView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            repo = RoleBindingRepository(session)
            existing = await repo.list_for_message(guild_id, message_id)
            buttons = [b for b in existing if b.interaction_type == RoleBindingType.BUTTON]
            if any(b.role_id == role_id for b in buttons):
                raise RoleBindingValidationError("That role already has a button on this message.")
            if len(buttons) >= 25:
                raise RoleBindingValidationError("This message already has the maximum of 25 buttons.")
            binding = await repo.create(
                guild_id,
                interaction_type=RoleBindingType.BUTTON,
                source_channel_id=channel_id,
                source_message_id=message_id,
                role_id=role_id,
                component_custom_id=custom_id,
                emoji=emoji,
            )
            return _to_view(binding)

    async def create_select_binding(
        self, guild_id: int, *, channel_id: int, message_id: int, role_id: int
    ) -> RoleBindingView:
        """Add one role option to the (single, implicit) select menu on a message.

        component_custom_id is set to the binding's own row id *after* insert,
        used as the SelectOption value - the select component's own custom_id
        is derived deterministically from message_id and never persisted, so
        adding this needed no schema change.
        """
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            repo = RoleBindingRepository(session)
            existing = await repo.list_for_message(guild_id, message_id)
            options = [b for b in existing if b.interaction_type == RoleBindingType.SELECT]
            if any(b.role_id == role_id for b in options):
                raise RoleBindingValidationError("That role is already an option in this menu.")
            if len(options) >= 25:
                raise RoleBindingValidationError("This menu already has the maximum of 25 options.")
            binding = await repo.create(
                guild_id,
                interaction_type=RoleBindingType.SELECT,
                source_channel_id=channel_id,
                source_message_id=message_id,
                role_id=role_id,
            )
            binding.component_custom_id = str(binding.id)
            await session.flush()
            return _to_view(binding)

    async def find_button_binding(
        self, guild_id: int, message_id: int, custom_id: str
    ) -> RoleBindingView | None:
        async with session_scope() as session:
            binding = await RoleBindingRepository(session).get_by_message_and_custom_id(
                guild_id, message_id, custom_id
            )
            return _to_view(binding) if binding is not None else None

    async def get_binding(self, guild_id: int, binding_id: int) -> RoleBindingView | None:
        async with session_scope() as session:
            binding = await RoleBindingRepository(session).get(guild_id, binding_id)
            return _to_view(binding) if binding is not None else None

    async def list_for_message(
        self, guild_id: int, message_id: int, interaction_type: RoleBindingType | None = None
    ) -> list[RoleBindingView]:
        async with session_scope() as session:
            bindings = await RoleBindingRepository(session).list_for_message(guild_id, message_id)
            views = [_to_view(b) for b in bindings]
        if interaction_type is not None:
            views = [v for v in views if v.interaction_type == interaction_type]
        return views

    async def remove_binding_by_role(
        self, guild_id: int, message_id: int, role_id: int, interaction_type: RoleBindingType
    ) -> bool:
        async with session_scope() as session:
            repo = RoleBindingRepository(session)
            existing = await repo.list_for_message(guild_id, message_id)
            match = next(
                (
                    b
                    for b in existing
                    if b.interaction_type == interaction_type and b.role_id == role_id
                ),
                None,
            )
            if match is None:
                return False
            return await repo.delete(guild_id, match.id)

    async def delete_binding(self, guild_id: int, binding_id: int) -> bool:
        async with session_scope() as session:
            return await RoleBindingRepository(session).delete(guild_id, binding_id)

    async def delete_message_bindings(self, guild_id: int, message_id: int) -> int:
        async with session_scope() as session:
            return await RoleBindingRepository(session).delete_for_message(guild_id, message_id)

    async def delete_message_bindings_by_type(
        self, guild_id: int, message_id: int, interaction_type: RoleBindingType
    ) -> int:
        async with session_scope() as session:
            return await RoleBindingRepository(session).delete_for_message_and_type(
                guild_id, message_id, interaction_type
            )

    async def set_message_enabled(self, guild_id: int, message_id: int, enabled: bool) -> int:
        async with session_scope() as session:
            repo = RoleBindingRepository(session)
            bindings = await repo.list_for_message(guild_id, message_id)
            for binding in bindings:
                binding.enabled = enabled
            await session.flush()
            return len(bindings)

    async def list_bindings(
        self, guild_id: int, interaction_type: RoleBindingType | None = None
    ) -> list[RoleBindingView]:
        async with session_scope() as session:
            bindings = await RoleBindingRepository(session).list_for_guild(guild_id)
            views = [_to_view(b) for b in bindings]
        if interaction_type is not None:
            views = [v for v in views if v.interaction_type == interaction_type]
        return views

    async def list_message_locations(self, guild_id: int) -> list[tuple[int, int]]:
        async with session_scope() as session:
            return await RoleBindingRepository(session).all_message_locations(guild_id)
