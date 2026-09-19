from __future__ import annotations

from app.db.models.welcome_config import WelcomeConfig
from app.db.repositories.base import BaseRepository


class WelcomeConfigRepository(BaseRepository):
    async def get(self, guild_id: int) -> WelcomeConfig | None:
        return await self.session.get(WelcomeConfig, guild_id)

    async def get_or_create(self, guild_id: int) -> WelcomeConfig:
        config = await self.get(guild_id)
        if config is not None:
            return config

        config = WelcomeConfig(guild_id=guild_id)
        self.session.add(config)
        await self.session.flush()
        return config

    async def set_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.enabled = enabled
        await self.session.flush()
        return config

    async def set_channel(self, guild_id: int, channel_id: int | None) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.channel_id = channel_id
        await self.session.flush()
        return config

    async def set_message_template(self, guild_id: int, template: str | None) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.message_template = template
        await self.session.flush()
        return config

    async def set_message_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.message_enabled = enabled
        await self.session.flush()
        return config

    async def set_embed_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.embed_enabled = enabled
        await self.session.flush()
        return config

    async def set_embed_title(self, guild_id: int, title: str | None) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.embed_title = title
        await self.session.flush()
        return config

    async def set_embed_description(self, guild_id: int, description: str | None) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.embed_description = description
        await self.session.flush()
        return config

    async def set_embed_footer(self, guild_id: int, footer: str | None) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.embed_footer = footer
        await self.session.flush()
        return config

    async def set_dm_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.dm_enabled = enabled
        await self.session.flush()
        return config

    async def set_dm_template(self, guild_id: int, template: str | None) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.dm_template = template
        await self.session.flush()
        return config

    async def set_role(self, guild_id: int, role_id: int) -> WelcomeConfig:
        """Set the auto-role and enable it in one call - configuring a role implies wanting it active."""
        config = await self.get_or_create(guild_id)
        config.role_id = role_id
        config.role_enabled = True
        await self.session.flush()
        return config

    async def disable_role(self, guild_id: int) -> WelcomeConfig:
        """Disable auto-role assignment without forgetting the configured role_id."""
        config = await self.get_or_create(guild_id)
        config.role_enabled = False
        await self.session.flush()
        return config

    async def set_join_log_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfig:
        config = await self.get_or_create(guild_id)
        config.join_log_enabled = enabled
        await self.session.flush()
        return config

    async def reset(self, guild_id: int) -> WelcomeConfig:
        """Reset every configurable field to its default, keeping the row."""
        config = await self.get_or_create(guild_id)
        config.enabled = False
        config.channel_id = None
        config.message_enabled = True
        config.message_template = None
        config.embed_enabled = False
        config.embed_title = None
        config.embed_description = None
        config.embed_footer = None
        config.dm_enabled = False
        config.dm_template = None
        config.role_enabled = False
        config.role_id = None
        config.join_log_enabled = False
        await self.session.flush()
        return config
