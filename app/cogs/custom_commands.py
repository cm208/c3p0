"""Custom commands cog.

Custom commands are triggered by exact-match text typed in chat (an
on_message listener, not a registered discord.py Command), and managed
through the /customcommand slash command group. Response text is DATA - it
is only ever rendered through app/utils/templates.py's explicit variable
map, never eval/exec/shell/SQL/HTTP (see app/db/models/custom_command.py's
own docstring).

Trigger/built-in collision is checked once, at creation time, by stripping
the guild's current prefix and checking it against bot.all_commands - see
_check_trigger_collision. A prefix change after the fact can't silently
resurrect a collision because trigger matching here is an exact literal
string compare, independent of whatever the current prefix is.
"""

from __future__ import annotations

import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.metrics import CUSTOM_COMMAND_INVOCATIONS
from app.services.config_service import ConfigurationService
from app.services.custom_command_service import (
    CustomCommandService,
    CustomCommandValidationError,
    CustomCommandView,
)
from app.services.event_log_service import EventLogService, EventTag
from app.utils.templates import TemplateContext, uptime_since

logger = logging.getLogger(__name__)

_LIST_DESCRIPTION_LIMIT = 3900


def _build_context(
    member: discord.Member, channel: discord.abc.GuildChannel, *, uptime: str = ""
) -> TemplateContext:
    return TemplateContext(
        user_display_name=str(member),
        user_mention=member.mention,
        user_id=member.id,
        guild_name=member.guild.name,
        member_count=member.guild.member_count or 0,
        channel_name=getattr(channel, "name", ""),
        channel_mention=getattr(channel, "mention", ""),
        uptime=uptime,
    )


class CustomCommandsCog(commands.Cog, name="CustomCommands"):
    customcommand_group = app_commands.Group(
        name="customcommand", description="Configure custom commands for this server."
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = CustomCommandService(default_prefix=bot.default_prefix)
        self.config_service = ConfigurationService(default_prefix=bot.default_prefix)
        self.events = EventLogService(default_prefix=bot.default_prefix)

    # --- Invocation ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return

        content = message.content.strip()
        if not content:
            return
        trigger = content.split(maxsplit=1)[0]

        try:
            view = await self.service.get_by_trigger(message.guild.id, trigger)
        except Exception:
            logger.exception("Failed to look up custom command", extra={"guild_id": message.guild.id})
            return
        if view is None or not view.enabled:
            return

        member = message.author
        member_role_ids = {role.id for role in member.roles}
        member_permission_names = {name for name, granted in member.guild_permissions if granted}
        if not self.service.check_restriction(
            view, member_role_ids=member_role_ids, member_permission_names=member_permission_names
        ):
            return  # Silent - a restricted command's existence isn't advertised to non-qualifying users.

        remaining = self.service.check_cooldown(view, member.id)
        if remaining is not None:
            await message.channel.send(f"⏳ That command is on cooldown for {int(remaining) + 1}s.", delete_after=5)
            return

        context = _build_context(
            member, message.channel, uptime=uptime_since(getattr(self.bot, "started_monotonic", None))
        )
        rendered = self.service.render_response(view, context)

        try:
            if view.embed_enabled:
                await message.channel.send(embed=discord.Embed(description=rendered, color=discord.Color.blurple()))
            else:
                await message.channel.send(rendered)
        except discord.HTTPException:
            logger.exception("Failed to send custom command response", extra={"guild_id": message.guild.id})
            return

        self.service.record_usage(view, member.id)
        CUSTOM_COMMAND_INVOCATIONS.inc()
        try:
            await self.service.increment_use_count(view)
        except Exception:
            # The response already went out - a failed counter bump
            # mustn't surface as an error for the user.
            logger.exception("Failed to increment custom command use count", extra={"guild_id": message.guild.id})
        await self.events.record(message.guild.id, EventTag.CMD, f"{view.trigger} invoked by @{member}")
        if view.usage_logging_enabled:
            logger.info(
                "Custom command invoked",
                extra={"guild_id": message.guild.id, "command_id": view.id, "user_id": member.id},
            )

    # --- Helpers ---

    async def _check_trigger_collision(self, guild_id: int, trigger: str) -> str | None:
        """Return the colliding built-in command name, or None if the trigger is safe to use."""
        config = await self.config_service.get_config(guild_id)
        prefix = config.prefix
        if not trigger.startswith(prefix):
            return None
        command_word = trigger[len(prefix) :].split(maxsplit=1)[0] if trigger[len(prefix) :] else ""
        if command_word and command_word in self.bot.all_commands:
            return command_word
        return None

    async def _get_or_not_found(
        self, interaction: discord.Interaction, trigger: str
    ) -> CustomCommandView | None:
        assert interaction.guild is not None
        view = await self.service.get_by_trigger(interaction.guild.id, trigger)
        if view is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
        return view

    # --- /customcommand commands ---

    @customcommand_group.command(name="create", description="Create a new custom command.")
    @app_commands.describe(
        name="A short display name for this command.",
        trigger="The exact text that triggers it, e.g. !rules.",
        response="Supports {user}, {user_mention}, {user_id}, {server}, {member_count}, {channel}, {channel_mention}.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_create(
        self, interaction: discord.Interaction, name: str, trigger: str, response: str
    ) -> None:
        assert interaction.guild is not None

        collision = await self._check_trigger_collision(interaction.guild.id, trigger)
        if collision:
            await interaction.response.send_message(
                f"⚠️ `{collision}` is already a built-in command - choose a different trigger.", ephemeral=True
            )
            return

        try:
            await self.service.create(
                interaction.guild.id,
                name=name,
                trigger=trigger,
                response=response,
                created_by=interaction.user.id,
            )
        except CustomCommandValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        logger.info(
            "Custom command created",
            extra={"guild_id": interaction.guild.id, "trigger": trigger, "moderator_id": interaction.user.id},
        )
        await interaction.response.send_message(f"✅ Created `{trigger}`.", ephemeral=True)

    @customcommand_group.command(name="edit", description="Change an existing command's response text.")
    @app_commands.describe(trigger="The command to edit.", response="The new response text.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_edit(self, interaction: discord.Interaction, trigger: str, response: str) -> None:
        assert interaction.guild is not None
        try:
            updated = await self.service.set_response_by_trigger(interaction.guild.id, trigger, response)
        except CustomCommandValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        if updated is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return

        logger.info(
            "Custom command edited",
            extra={"guild_id": interaction.guild.id, "trigger": trigger, "moderator_id": interaction.user.id},
        )
        await interaction.response.send_message(f"✅ Updated `{trigger}`.", ephemeral=True)

    @customcommand_group.command(name="delete", description="Delete a custom command.")
    @app_commands.describe(trigger="The command to delete.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_delete(self, interaction: discord.Interaction, trigger: str) -> None:
        assert interaction.guild is not None
        removed = await self.service.delete_by_trigger(interaction.guild.id, trigger)
        if not removed:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return

        logger.info(
            "Custom command deleted",
            extra={"guild_id": interaction.guild.id, "trigger": trigger, "moderator_id": interaction.user.id},
        )
        await interaction.response.send_message(f"✅ Deleted `{trigger}`.", ephemeral=True)

    @customcommand_group.command(name="enable", description="Enable a custom command.")
    @app_commands.describe(trigger="The command to enable.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_enable(self, interaction: discord.Interaction, trigger: str) -> None:
        assert interaction.guild is not None
        updated = await self.service.set_enabled_by_trigger(interaction.guild.id, trigger, True)
        if updated is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Enabled `{trigger}`.", ephemeral=True)

    @customcommand_group.command(name="disable", description="Disable a custom command.")
    @app_commands.describe(trigger="The command to disable.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_disable(self, interaction: discord.Interaction, trigger: str) -> None:
        assert interaction.guild is not None
        updated = await self.service.set_enabled_by_trigger(interaction.guild.id, trigger, False)
        if updated is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Disabled `{trigger}`.", ephemeral=True)

    @customcommand_group.command(name="embed", description="Toggle whether the response is sent as an embed.")
    @app_commands.describe(trigger="The command to update.", enabled="Send as an embed instead of plain text.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_embed(self, interaction: discord.Interaction, trigger: str, enabled: bool) -> None:
        assert interaction.guild is not None
        updated = await self.service.set_embed_enabled_by_trigger(interaction.guild.id, trigger, enabled)
        if updated is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Embed mode {'enabled' if enabled else 'disabled'} for `{trigger}`.", ephemeral=True)

    @customcommand_group.command(name="restrict", description="Restrict who can use a command.")
    @app_commands.describe(
        trigger="The command to restrict.",
        type="public (anyone), role (a specific role), or permission (a Discord permission).",
        role="Required role, if type is role.",
        permission="Required permission name (e.g. manage_messages), if type is permission.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_restrict(
        self,
        interaction: discord.Interaction,
        trigger: str,
        type: Literal["public", "role", "permission"],
        role: discord.Role | None = None,
        permission: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        if type == "permission" and permission is not None and permission not in discord.Permissions.VALID_FLAGS:
            await interaction.response.send_message(f"⚠️ `{permission}` isn't a real Discord permission.", ephemeral=True)
            return

        try:
            updated = await self.service.set_restriction_by_trigger(
                interaction.guild.id,
                trigger,
                restriction_type=type,
                restricted_role_id=role.id if role else None,
                restricted_permission=permission,
            )
        except CustomCommandValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        if updated is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ `{trigger}` is now restricted to: {type}.", ephemeral=True)

    @customcommand_group.command(name="cooldown", description="Set a cooldown on a command.")
    @app_commands.describe(
        trigger="The command to update.",
        type="none, per-user, or per-guild (shared) cooldown.",
        seconds="Cooldown length in seconds.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_cooldown(
        self,
        interaction: discord.Interaction,
        trigger: str,
        type: Literal["none", "user", "guild"],
        seconds: int = 0,
    ) -> None:
        assert interaction.guild is not None
        try:
            updated = await self.service.set_cooldown_by_trigger(
                interaction.guild.id, trigger, cooldown_type=type, cooldown_seconds=seconds
            )
        except CustomCommandValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        if updated is None:
            await interaction.response.send_message(f"⚠️ No command with trigger `{trigger}` found.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Cooldown for `{trigger}` set to {type}/{seconds}s.", ephemeral=True)

    @customcommand_group.command(name="list", description="List all custom commands in this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        commands_ = await self.service.list_for_guild(interaction.guild.id)
        if not commands_:
            await interaction.response.send_message("No custom commands configured.", ephemeral=True)
            return

        lines = [f"{'✅' if c.enabled else '⏸️'} **{c.name}** - `{c.trigger}`" for c in commands_]
        description = "\n".join(lines)
        if len(description) > _LIST_DESCRIPTION_LIMIT:
            description = description[:_LIST_DESCRIPTION_LIMIT] + "\n…and more"

        embed = discord.Embed(title="Custom Commands", description=description, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @customcommand_group.command(name="show", description="Show a custom command's full configuration.")
    @app_commands.describe(trigger="The command to show.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def customcommand_show(self, interaction: discord.Interaction, trigger: str) -> None:
        assert interaction.guild is not None
        view = await self._get_or_not_found(interaction, trigger)
        if view is None:
            return

        embed = discord.Embed(title=f"Custom Command: {view.name}", color=discord.Color.blurple())
        embed.add_field(name="Trigger", value=f"`{view.trigger}`", inline=True)
        embed.add_field(name="Enabled", value="Yes" if view.enabled else "No", inline=True)
        embed.add_field(name="Embed mode", value="Yes" if view.embed_enabled else "No", inline=True)
        embed.add_field(name="Restriction", value=view.restriction_type, inline=True)
        embed.add_field(
            name="Cooldown",
            value=f"{view.cooldown_type} ({view.cooldown_seconds}s)" if view.cooldown_type != "none" else "None",
            inline=True,
        )
        response_preview = view.response if len(view.response) <= 900 else view.response[:899] + "…"
        embed.add_field(name="Response", value=f"```{response_preview}```", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomCommandsCog(bot))
