from __future__ import annotations

from sqlalchemy import select

from app.db.models.role_binding import RoleBinding, RoleBindingType
from app.db.repositories.base import BaseRepository


class RoleBindingRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        *,
        interaction_type: RoleBindingType,
        source_channel_id: int,
        source_message_id: int,
        role_id: int,
        emoji: str | None = None,
        component_custom_id: str | None = None,
        toggle: bool = True,
    ) -> RoleBinding:
        binding = RoleBinding(
            guild_id=guild_id,
            interaction_type=interaction_type,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            role_id=role_id,
            emoji=emoji,
            component_custom_id=component_custom_id,
            toggle=toggle,
        )
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def get(self, guild_id: int, binding_id: int) -> RoleBinding | None:
        stmt = select(RoleBinding).where(
            RoleBinding.id == binding_id, RoleBinding.guild_id == guild_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_guild(self, guild_id: int) -> list[RoleBinding]:
        stmt = select(RoleBinding).where(RoleBinding.guild_id == guild_id).order_by(RoleBinding.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_for_message(self, guild_id: int, message_id: int) -> list[RoleBinding]:
        stmt = select(RoleBinding).where(
            RoleBinding.guild_id == guild_id, RoleBinding.source_message_id == message_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_by_message_and_emoji(
        self, guild_id: int, message_id: int, emoji: str
    ) -> RoleBinding | None:
        stmt = select(RoleBinding).where(
            RoleBinding.guild_id == guild_id,
            RoleBinding.source_message_id == message_id,
            RoleBinding.interaction_type == RoleBindingType.REACTION,
            RoleBinding.emoji == emoji,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_message_and_custom_id(
        self, guild_id: int, message_id: int, custom_id: str
    ) -> RoleBinding | None:
        stmt = select(RoleBinding).where(
            RoleBinding.guild_id == guild_id,
            RoleBinding.source_message_id == message_id,
            RoleBinding.component_custom_id == custom_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_enabled(self, guild_id: int, binding_id: int, enabled: bool) -> RoleBinding | None:
        binding = await self.get(guild_id, binding_id)
        if binding is None:
            return None
        binding.enabled = enabled
        await self.session.flush()
        return binding

    async def delete(self, guild_id: int, binding_id: int) -> bool:
        binding = await self.get(guild_id, binding_id)
        if binding is None:
            return False
        await self.session.delete(binding)
        await self.session.flush()
        return True

    async def delete_for_message(self, guild_id: int, message_id: int) -> int:
        bindings = await self.list_for_message(guild_id, message_id)
        for binding in bindings:
            await self.session.delete(binding)
        await self.session.flush()
        return len(bindings)

    async def delete_for_message_and_type(
        self, guild_id: int, message_id: int, interaction_type: RoleBindingType
    ) -> int:
        bindings = [
            b
            for b in await self.list_for_message(guild_id, message_id)
            if b.interaction_type == interaction_type
        ]
        for binding in bindings:
            await self.session.delete(binding)
        await self.session.flush()
        return len(bindings)

    async def all_message_locations(self, guild_id: int) -> list[tuple[int, int]]:
        """Distinct (channel_id, message_id) pairs with at least one binding - for diagnostics."""
        stmt = (
            select(RoleBinding.source_channel_id, RoleBinding.source_message_id)
            .where(RoleBinding.guild_id == guild_id)
            .distinct()
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]
