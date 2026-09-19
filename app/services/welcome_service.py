"""Welcome/member-join configuration and rendering service.

Like ConfigurationService, this takes/returns plain Python - no discord.py
types - so a future dashboard could call it directly. Discord objects
(Member, Guild, TextChannel) are the cog's job to translate into the plain
JoinContext (an alias of app.utils.templates.TemplateContext - custom
commands render against the identical shape, see that module) this
service accepts for rendering.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.welcome_config import WelcomeConfig
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.welcome_config_repository import WelcomeConfigRepository
from app.utils.templates import (
    STANDARD_VARIABLES as WELCOME_VARIABLES,
)
from app.utils.templates import (
    TemplateContext as JoinContext,
)
from app.utils.templates import (
    UnknownTemplateVariableError,
    render_template_context,
    validate_template,
)

MAX_MESSAGE_LENGTH = 2000  # Discord message content limit
MAX_EMBED_TITLE_LENGTH = 256
MAX_EMBED_DESCRIPTION_LENGTH = 4096
MAX_EMBED_FOOTER_LENGTH = 2048

DEFAULT_MESSAGE_TEMPLATE = "Welcome {user_mention} to {server}! 🎉"
DEFAULT_DM_TEMPLATE = "Welcome to {server}!"


class WelcomeValidationError(Exception):
    """Raised for invalid welcome configuration input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class WelcomeConfigView:
    guild_id: int
    enabled: bool
    channel_id: int | None
    message_enabled: bool
    message_template: str | None
    embed_enabled: bool
    embed_title: str | None
    embed_description: str | None
    embed_footer: str | None
    dm_enabled: bool
    dm_template: str | None
    role_enabled: bool
    role_id: int | None
    join_log_enabled: bool


def _to_view(config: WelcomeConfig) -> WelcomeConfigView:
    return WelcomeConfigView(
        guild_id=config.guild_id,
        enabled=config.enabled,
        channel_id=config.channel_id,
        message_enabled=config.message_enabled,
        message_template=config.message_template,
        embed_enabled=config.embed_enabled,
        embed_title=config.embed_title,
        embed_description=config.embed_description,
        embed_footer=config.embed_footer,
        dm_enabled=config.dm_enabled,
        dm_template=config.dm_template,
        role_enabled=config.role_enabled,
        role_id=config.role_id,
        join_log_enabled=config.join_log_enabled,
    )


def _validate_text(value: str, *, field: str, max_length: int, allow_variables: bool = False) -> str:
    value = value.strip()
    if not value:
        raise WelcomeValidationError(f"{field} cannot be empty.")
    if len(value) > max_length:
        raise WelcomeValidationError(f"{field} must be {max_length} characters or fewer.")
    if allow_variables:
        try:
            validate_template(value, WELCOME_VARIABLES)
        except UnknownTemplateVariableError as exc:
            raise WelcomeValidationError(str(exc)) from exc
    return value


class WelcomeService:
    def __init__(self, default_prefix: str = "!") -> None:
        # Only needed so that configuring welcome before ever running
        # /config still creates a GuildConfig row with the right default
        # prefix, rather than silently hardcoding "!".
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # WelcomeConfig.guild_id FKs to guild_config.guild_id - ensure the
        # parent row exists first so the relationship is always meaningful,
        # not just permitted by SQLite's lack of FK enforcement.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def get_config(self, guild_id: int) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).get_or_create(guild_id)
            return _to_view(config)

    async def set_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_enabled(guild_id, enabled)
            return _to_view(config)

    async def set_channel(self, guild_id: int, channel_id: int | None) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_channel(guild_id, channel_id)
            return _to_view(config)

    async def set_message(self, guild_id: int, template: str) -> WelcomeConfigView:
        template = _validate_text(
            template, field="Message", max_length=MAX_MESSAGE_LENGTH, allow_variables=True
        )
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_message_template(guild_id, template)
            return _to_view(config)

    async def set_message_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfigView:
        # The repository method has existed since step 7 but was never
        # wired up to a Discord command - message_enabled has always
        # effectively been permanently True in practice. Exposed here so
        # the web dashboard can actually toggle it, matching its sibling
        # toggles (dm_enabled, embed_enabled, etc.) exactly.
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_message_enabled(guild_id, enabled)
            return _to_view(config)

    async def set_dm_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_dm_enabled(guild_id, enabled)
            return _to_view(config)

    async def set_dm_message(self, guild_id: int, template: str) -> WelcomeConfigView:
        template = _validate_text(
            template, field="DM message", max_length=MAX_MESSAGE_LENGTH, allow_variables=True
        )
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_dm_template(guild_id, template)
            return _to_view(config)

    async def set_role(self, guild_id: int, role_id: int) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_role(guild_id, role_id)
            return _to_view(config)

    async def disable_role(self, guild_id: int) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).disable_role(guild_id)
            return _to_view(config)

    async def set_embed_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_embed_enabled(guild_id, enabled)
            return _to_view(config)

    async def set_embed_title(self, guild_id: int, title: str | None) -> WelcomeConfigView:
        if title is not None:
            title = _validate_text(
                title, field="Embed title", max_length=MAX_EMBED_TITLE_LENGTH, allow_variables=True
            )
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_embed_title(guild_id, title)
            return _to_view(config)

    async def set_embed_description(self, guild_id: int, description: str | None) -> WelcomeConfigView:
        if description is not None:
            description = _validate_text(
                description,
                field="Embed description",
                max_length=MAX_EMBED_DESCRIPTION_LENGTH,
                allow_variables=True,
            )
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_embed_description(guild_id, description)
            return _to_view(config)

    async def set_embed_footer(self, guild_id: int, footer: str | None) -> WelcomeConfigView:
        if footer is not None:
            footer = _validate_text(
                footer, field="Embed footer", max_length=MAX_EMBED_FOOTER_LENGTH, allow_variables=True
            )
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_embed_footer(guild_id, footer)
            return _to_view(config)

    async def set_join_log_enabled(self, guild_id: int, enabled: bool) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).set_join_log_enabled(guild_id, enabled)
            return _to_view(config)

    async def reset(self, guild_id: int) -> WelcomeConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await WelcomeConfigRepository(session).reset(guild_id)
            return _to_view(config)

    def render_join_message(self, template: str, context: JoinContext) -> str:
        return render_template_context(template, context)
