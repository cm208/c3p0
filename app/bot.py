"""Core bot class.

Required Discord intents (see docs/discord-setup.md):
    - guilds: baseline, required for almost everything.
    - members: privileged. Required for member-join events (welcome system)
      and accurate role/member state for moderation and role assignment.
    - guild_messages: required to receive MESSAGE_CREATE events for guild
      channels at all - without it the bot never sees `!`-prefixed commands
      or custom-command triggers, regardless of message_content below.
    - message_content: privileged. Required because C3P0 uses `!`-prefixed
      text commands and custom commands, which need to read message text -
      but only controls whether content is populated on messages the bot
      already receives via guild_messages above, not whether it receives
      them.
    - voice_states: required for music playback.

Both privileged intents must also be enabled for the application in the
Discord Developer Portal, or the bot will fail to connect.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

import discord
import uvicorn
from discord.ext import commands, tasks

from app.config import Config
from app.db.database import session_scope
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.health import mark_healthy, mark_unhealthy
from app.metrics import COMMAND_FAILURES, COMMANDS_INVOKED, DISCORD_CONNECTED, GUILD_COUNT
from app.music.internal_api import create_internal_app
from app.services.bot_guild_service import BotGuildService
from app.services.music_service import MusicService

logger = logging.getLogger(__name__)

# Cog extension modules loaded at startup. Order doesn't matter functionally
# but is kept aligned with the order these features were originally built in.
INITIAL_EXTENSIONS: tuple[str, ...] = (
    "app.cogs.admin",
    "app.cogs.welcome",
    "app.cogs.roles",
    "app.cogs.moderation",
    "app.cogs.custom_commands",
    "app.cogs.music",
)

REQUIRED_INTENTS = discord.Intents.none()
REQUIRED_INTENTS.guilds = True
REQUIRED_INTENTS.members = True
REQUIRED_INTENTS.guild_messages = True
REQUIRED_INTENTS.message_content = True
REQUIRED_INTENTS.voice_states = True
REQUIRED_INTENTS.guild_reactions = True


async def _resolve_prefix(bot: C3P0Bot, message: discord.Message) -> list[str]:
    default = bot.default_prefix
    if message.guild is None:
        return commands.when_mentioned_or(default)(bot, message)

    prefix = bot.prefix_cache.get(message.guild.id)
    if prefix is None:
        try:
            async with session_scope() as session:
                repo = GuildConfigRepository(session)
                config = await repo.get(message.guild.id)
            prefix = config.prefix if config is not None else default
        except Exception:
            logger.exception("Failed to resolve guild prefix; falling back to default")
            prefix = default
        bot.prefix_cache[message.guild.id] = prefix

    return commands.when_mentioned_or(prefix)(bot, message)


class C3P0Bot(commands.Bot):
    def __init__(self, config: Config) -> None:
        super().__init__(
            command_prefix=_resolve_prefix,
            intents=REQUIRED_INTENTS,
            help_command=commands.DefaultHelpCommand(),
        )
        self.app_config = config
        self.default_prefix = config.default_prefix
        # Simple per-process cache of guild_id -> prefix, invalidated by the
        # admin cog whenever /config prefix changes.
        self.prefix_cache: dict[int, str] = {}
        self.tree.on_error = self.on_app_command_error
        # Mirrors DISCORD_CONNECTED but readable back from Python, so the
        # health heartbeat below knows whether it's safe to mark healthy.
        self._gateway_connected = False
        self.bot_guild_service = BotGuildService()
        self._internal_api_task: asyncio.Task[None] | None = None
        self._internal_api_server: uvicorn.Server | None = None

    async def setup_hook(self) -> None:
        for extension in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded extension", extra={"extension": extension})
            except commands.ExtensionError:
                logger.exception("Failed to load extension", extra={"extension": extension})

        music_cog = self.get_cog("Music")
        if music_cog is not None:
            self._internal_api_task = asyncio.create_task(self._run_internal_api(music_cog.service))
        else:
            logger.error("Music cog not loaded - internal music control API will not start")

        if self.app_config.dev_guild_ids:
            for guild_id in self.app_config.dev_guild_ids:
                guild_obj = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
            logger.info(
                "Synced slash commands to dev guilds",
                extra={"guild_ids": self.app_config.dev_guild_ids},
            )
        else:
            await self.tree.sync()
            logger.info("Synced slash commands globally")

        self._health_heartbeat.start()

    async def _run_internal_api(self, music_service: MusicService) -> None:
        """Serve the internal music control-plane API on the bot's own event
        loop (see app/music/internal_api.py) for the lifetime of the process.

        Mirrors app/web/__main__.py's exact uvicorn.Server(...).serve() call
        shape, including log_config=None for the same reason documented
        there (uvicorn's default startup would otherwise reconfigure the
        *root* logger and silently swallow every app-level logger.info()
        call).
        """
        internal_app = create_internal_app(
            self, music_service=music_service, internal_api_token=self.app_config.internal_api_token
        )
        self._internal_api_server = uvicorn.Server(
            uvicorn.Config(
                internal_app,
                host=self.app_config.internal_api_host,
                port=self.app_config.internal_api_port,
                log_config=None,
            )
        )
        await self._internal_api_server.serve()

    async def close(self) -> None:
        if self._internal_api_server is not None:
            self._internal_api_server.should_exit = True
        if self._internal_api_task is not None:
            await self._internal_api_task
        await super().close()

    async def on_ready(self) -> None:
        logger.info(
            "Bot ready",
            extra={"user": str(self.user), "guild_count": len(self.guilds)},
        )
        self._gateway_connected = True
        DISCORD_CONNECTED.set(1)
        GUILD_COUNT.set(len(self.guilds))
        mark_healthy()

        try:
            await self.bot_guild_service.reconcile({g.id: g.name for g in self.guilds})
        except Exception:
            # Never let a DB hiccup here break gateway handling - the web
            # dashboard's guild list would just be briefly stale, not fatal.
            logger.exception("Failed to reconcile bot guild membership")

    async def on_disconnect(self) -> None:
        self._gateway_connected = False
        DISCORD_CONNECTED.set(0)
        mark_unhealthy()

    async def on_resumed(self) -> None:
        self._gateway_connected = True
        DISCORD_CONNECTED.set(1)
        mark_healthy()

    @tasks.loop(seconds=30)
    async def _health_heartbeat(self) -> None:
        # on_ready/on_resumed only fire on (re)connect, so a session that
        # stays up without ever dropping would otherwise never refresh
        # /data/health and would eventually be reported unhealthy despite
        # being perfectly fine. This keeps it fresh for as long as the
        # gateway connection is actually alive.
        if self._gateway_connected:
            mark_healthy()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info("Joined guild", extra={"guild_id": guild.id})
        GUILD_COUNT.set(len(self.guilds))
        try:
            await self.bot_guild_service.mark_present(guild.id, guild.name)
        except Exception:
            logger.exception("Failed to record guild join", extra={"guild_id": guild.id})

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info("Removed from guild", extra={"guild_id": guild.id})
        self.prefix_cache.pop(guild.id, None)
        GUILD_COUNT.set(len(self.guilds))
        try:
            await self.bot_guild_service.mark_absent(guild.id)
        except Exception:
            logger.exception("Failed to record guild removal", extra={"guild_id": guild.id})

    async def on_command(self, ctx: commands.Context) -> None:
        category = ctx.cog.qualified_name if ctx.cog else "uncategorized"
        COMMANDS_INVOKED.labels(command=ctx.command.qualified_name, category=category).inc()

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        category = ctx.cog.qualified_name if ctx.cog else "uncategorized"
        command_name = ctx.command.qualified_name if ctx.command else "unknown"

        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.UserInputError):
            # Bad/missing arguments - discord.py's own message says what's
            # wrong (e.g. "query is a required argument that is missing"),
            # but not what's right. Append the actual usage so a moderator
            # who can only interact via Discord (no logs, no source) doesn't
            # have to guess the correct form.
            usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            await self._try_send(ctx, f"⚠️ {error}\nUsage: `{usage}`")
            return

        if isinstance(error, commands.CheckFailure):
            await self._try_send(ctx, f"⚠️ {error}")
            return

        COMMAND_FAILURES.labels(command=command_name, category=category).inc()
        logger.error(
            "Unhandled command error",
            extra={"command": command_name, "guild_id": ctx.guild.id if ctx.guild else None},
            exc_info=error,
        )
        # The underlying exception's message isn't shown - it could contain
        # internals (paths, raw API payloads) that shouldn't go to a random
        # Discord channel - but the exception *type* is safe and gives a
        # moderator a concrete starting point instead of just "it broke".
        # CommandInvokeError/ConversionError both wrap the real exception in
        # `.original` rather than just Python's __cause__ chaining.
        cause = getattr(error, "original", error)
        await self._try_send(
            ctx, f"⚠️ Something went wrong running that command ({type(cause).__name__}). It's been logged."
        )

    @staticmethod
    async def _try_send(ctx: commands.Context, content: str) -> None:
        # The bot can lack Send Messages in the channel a command was run
        # in (a channel-specific overwrite is the common cause) - that's
        # often *why* the command itself just failed, so this reply attempt
        # can hit the same Forbidden. Letting that propagate out of
        # on_command_error just produces a second, noisier "Unhandled event
        # error" traceback on top of the one already logged above; there's
        # nothing more useful to do here than swallow it.
        try:
            await ctx.send(content)
        except discord.Forbidden:
            pass

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: discord.app_commands.Command | discord.app_commands.ContextMenu,
    ) -> None:
        category = command.binding.qualified_name if command.binding else "uncategorized"
        COMMANDS_INVOKED.labels(command=command.qualified_name, category=category).inc()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        command = interaction.command
        command_name = command.qualified_name if command else "unknown"
        category = command.binding.qualified_name if command and command.binding else "uncategorized"

        if isinstance(error, discord.app_commands.CheckFailure):
            message = f"⚠️ {error}"
        else:
            COMMAND_FAILURES.labels(command=command_name, category=category).inc()
            logger.error(
                "Unhandled app command error",
                extra={
                    "command": command_name,
                    "guild_id": interaction.guild_id,
                },
                exc_info=error,
            )
            cause = getattr(error, "original", error)
            message = (
                f"⚠️ Something went wrong running that command ({type(cause).__name__}). "
                "It's been logged."
            )

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def on_error(self, event_method: str, /, *args: object, **kwargs: object) -> None:
        logger.error(
            "Unhandled event error",
            extra={"event": event_method},
            exc_info=True,
        )
        # Never let an event handler exception propagate and kill the bot.
        traceback.print_exc()
