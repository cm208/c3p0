from __future__ import annotations

from sqlalchemy import select

from app.db.models.custom_command import CustomCommand
from app.db.repositories.base import BaseRepository


class CustomCommandRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        *,
        name: str,
        trigger: str,
        response: str,
        created_by: int,
    ) -> CustomCommand:
        command = CustomCommand(
            guild_id=guild_id, name=name, trigger=trigger, response=response, created_by=created_by
        )
        self.session.add(command)
        await self.session.flush()
        return command

    async def get(self, guild_id: int, command_id: int) -> CustomCommand | None:
        stmt = select(CustomCommand).where(
            CustomCommand.id == command_id, CustomCommand.guild_id == guild_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_trigger(self, guild_id: int, trigger: str) -> CustomCommand | None:
        stmt = select(CustomCommand).where(
            CustomCommand.guild_id == guild_id, CustomCommand.trigger == trigger
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_guild(self, guild_id: int) -> list[CustomCommand]:
        stmt = (
            select(CustomCommand).where(CustomCommand.guild_id == guild_id).order_by(CustomCommand.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def delete(self, guild_id: int, command_id: int) -> bool:
        command = await self.get(guild_id, command_id)
        if command is None:
            return False
        await self.session.delete(command)
        await self.session.flush()
        return True

    async def set_enabled(self, guild_id: int, command_id: int, enabled: bool) -> CustomCommand | None:
        command = await self.get(guild_id, command_id)
        if command is None:
            return None
        command.enabled = enabled
        await self.session.flush()
        return command

    async def set_response(self, guild_id: int, command_id: int, response: str) -> CustomCommand | None:
        command = await self.get(guild_id, command_id)
        if command is None:
            return None
        command.response = response
        await self.session.flush()
        return command

    async def set_embed_enabled(
        self, guild_id: int, command_id: int, enabled: bool
    ) -> CustomCommand | None:
        command = await self.get(guild_id, command_id)
        if command is None:
            return None
        command.embed_enabled = enabled
        await self.session.flush()
        return command

    async def set_restriction(
        self,
        guild_id: int,
        command_id: int,
        *,
        restriction_type: str,
        restricted_role_id: int | None,
        restricted_permission: str | None,
    ) -> CustomCommand | None:
        command = await self.get(guild_id, command_id)
        if command is None:
            return None
        command.restriction_type = restriction_type
        command.restricted_role_id = restricted_role_id
        command.restricted_permission = restricted_permission
        await self.session.flush()
        return command

    async def set_cooldown(
        self, guild_id: int, command_id: int, *, cooldown_type: str, cooldown_seconds: int
    ) -> CustomCommand | None:
        command = await self.get(guild_id, command_id)
        if command is None:
            return None
        command.cooldown_type = cooldown_type
        command.cooldown_seconds = cooldown_seconds
        await self.session.flush()
        return command

    async def set_usage_logging(
        self, guild_id: int, command_id: int, enabled: bool
    ) -> CustomCommand | None:
        command = await self.get(guild_id, command_id)
        if command is None:
            return None
        command.usage_logging_enabled = enabled
        await self.session.flush()
        return command
