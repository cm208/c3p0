from __future__ import annotations

from app.db.models.music_config import MusicConfig
from app.db.repositories.base import BaseRepository


class MusicConfigRepository(BaseRepository):
    async def get(self, guild_id: int) -> MusicConfig | None:
        return await self.session.get(MusicConfig, guild_id)

    async def get_or_create(self, guild_id: int) -> MusicConfig:
        config = await self.get(guild_id)
        if config is not None:
            return config

        config = MusicConfig(guild_id=guild_id)
        self.session.add(config)
        await self.session.flush()
        return config

    async def set_default_volume(self, guild_id: int, volume: int) -> MusicConfig:
        config = await self.get_or_create(guild_id)
        config.default_volume = volume
        await self.session.flush()
        return config

    async def set_max_queue_size(self, guild_id: int, size: int) -> MusicConfig:
        config = await self.get_or_create(guild_id)
        config.max_queue_size = size
        await self.session.flush()
        return config

    async def set_dj_role(self, guild_id: int, role_id: int | None) -> MusicConfig:
        config = await self.get_or_create(guild_id)
        config.dj_role_id = role_id
        await self.session.flush()
        return config

    async def set_music_channel(self, guild_id: int, channel_id: int | None) -> MusicConfig:
        config = await self.get_or_create(guild_id)
        config.music_channel_id = channel_id
        await self.session.flush()
        return config

    async def set_enabled(self, guild_id: int, enabled: bool) -> MusicConfig:
        config = await self.get_or_create(guild_id)
        config.enabled = enabled
        await self.session.flush()
        return config
