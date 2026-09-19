"""Core guild configuration service.

Services take plain Python inputs (guild IDs, primitive values) and return
plain Python outputs - never discord.Interaction or other Discord objects -
so a future HTTP API/dashboard can call the same service layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.database import session_scope
from app.db.repositories.guild_config_repository import GuildConfigRepository

MAX_PREFIX_LENGTH = 10


class ConfigValidationError(Exception):
    """Raised for invalid configuration input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class GuildConfigView:
    guild_id: int
    prefix: str
    default_role_id: int | None
    log_channel_id: int | None
    moderation_log_channel_id: int | None


class ConfigurationService:
    def __init__(self, default_prefix: str = "!") -> None:
        self._default_prefix = default_prefix

    async def get_config(self, guild_id: int) -> GuildConfigView:
        async with session_scope() as session:
            repo = GuildConfigRepository(session)
            config = await repo.get_or_create(guild_id, default_prefix=self._default_prefix)
            return GuildConfigView(
                guild_id=config.guild_id,
                prefix=config.prefix,
                default_role_id=config.default_role_id,
                log_channel_id=config.log_channel_id,
                moderation_log_channel_id=config.moderation_log_channel_id,
            )

    async def set_prefix(self, guild_id: int, prefix: str) -> GuildConfigView:
        prefix = prefix.strip()
        if not prefix:
            raise ConfigValidationError("Prefix cannot be empty.")
        if len(prefix) > MAX_PREFIX_LENGTH:
            raise ConfigValidationError(f"Prefix must be {MAX_PREFIX_LENGTH} characters or fewer.")
        if any(ch.isspace() for ch in prefix):
            raise ConfigValidationError("Prefix cannot contain whitespace.")

        async with session_scope() as session:
            repo = GuildConfigRepository(session)
            config = await repo.set_prefix(guild_id, prefix)
            return GuildConfigView(
                guild_id=config.guild_id,
                prefix=config.prefix,
                default_role_id=config.default_role_id,
                log_channel_id=config.log_channel_id,
                moderation_log_channel_id=config.moderation_log_channel_id,
            )

    async def set_default_role(self, guild_id: int, role_id: int | None) -> GuildConfigView:
        async with session_scope() as session:
            repo = GuildConfigRepository(session)
            config = await repo.set_default_role(guild_id, role_id)
            return GuildConfigView(
                guild_id=config.guild_id,
                prefix=config.prefix,
                default_role_id=config.default_role_id,
                log_channel_id=config.log_channel_id,
                moderation_log_channel_id=config.moderation_log_channel_id,
            )

    async def set_log_channel(self, guild_id: int, channel_id: int | None) -> GuildConfigView:
        async with session_scope() as session:
            repo = GuildConfigRepository(session)
            config = await repo.set_log_channel(guild_id, channel_id)
            return GuildConfigView(
                guild_id=config.guild_id,
                prefix=config.prefix,
                default_role_id=config.default_role_id,
                log_channel_id=config.log_channel_id,
                moderation_log_channel_id=config.moderation_log_channel_id,
            )

    async def set_moderation_log_channel(self, guild_id: int, channel_id: int | None) -> GuildConfigView:
        async with session_scope() as session:
            repo = GuildConfigRepository(session)
            config = await repo.set_moderation_log_channel(guild_id, channel_id)
            return GuildConfigView(
                guild_id=config.guild_id,
                prefix=config.prefix,
                default_role_id=config.default_role_id,
                log_channel_id=config.log_channel_id,
                moderation_log_channel_id=config.moderation_log_channel_id,
            )
