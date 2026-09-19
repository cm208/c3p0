from __future__ import annotations

from app.db.models.moderation_config import ModerationConfig
from app.db.repositories.base import BaseRepository


class ModerationConfigRepository(BaseRepository):
    async def get(self, guild_id: int) -> ModerationConfig | None:
        return await self.session.get(ModerationConfig, guild_id)

    async def get_or_create(self, guild_id: int) -> ModerationConfig:
        config = await self.get(guild_id)
        if config is not None:
            return config

        config = ModerationConfig(guild_id=guild_id)
        self.session.add(config)
        await self.session.flush()
        return config

    async def set_escalation_enabled(self, guild_id: int, enabled: bool) -> ModerationConfig:
        config = await self.get_or_create(guild_id)
        config.escalation_enabled = enabled
        await self.session.flush()
        return config

    async def set_escalation_thresholds(
        self, guild_id: int, thresholds: dict[str, str]
    ) -> ModerationConfig:
        config = await self.get_or_create(guild_id)
        config.escalation_thresholds = thresholds
        await self.session.flush()
        return config
