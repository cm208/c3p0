from __future__ import annotations

from sqlalchemy import select

from app.db.models.guild_config import GuildConfig
from app.db.repositories.base import BaseRepository


class GuildConfigRepository(BaseRepository):
    async def get(self, guild_id: int) -> GuildConfig | None:
        return await self.session.get(GuildConfig, guild_id)

    async def get_or_create(self, guild_id: int, *, default_prefix: str = "!") -> GuildConfig:
        config = await self.get(guild_id)
        if config is not None:
            return config

        config = GuildConfig(guild_id=guild_id, prefix=default_prefix)
        self.session.add(config)
        await self.session.flush()
        return config

    async def set_prefix(self, guild_id: int, prefix: str) -> GuildConfig:
        config = await self.get_or_create(guild_id)
        config.prefix = prefix
        await self.session.flush()
        return config

    async def set_default_role(self, guild_id: int, role_id: int | None) -> GuildConfig:
        config = await self.get_or_create(guild_id)
        config.default_role_id = role_id
        await self.session.flush()
        return config

    async def set_log_channel(self, guild_id: int, channel_id: int | None) -> GuildConfig:
        config = await self.get_or_create(guild_id)
        config.log_channel_id = channel_id
        await self.session.flush()
        return config

    async def set_moderation_log_channel(self, guild_id: int, channel_id: int | None) -> GuildConfig:
        config = await self.get_or_create(guild_id)
        config.moderation_log_channel_id = channel_id
        await self.session.flush()
        return config

    async def all_guild_ids(self) -> list[int]:
        result = await self.session.execute(select(GuildConfig.guild_id))
        return [row[0] for row in result.all()]
