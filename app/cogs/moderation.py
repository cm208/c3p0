"""Moderation cog.

Prefix commands (!) rather than slash commands - the first feature to use
them, everything before this has been slash commands. Escalation
configuration is still a slash command group (/moderation escalation ...)
since that's admin configuration, not a moderation action.

Every action follows the same permission-and-hierarchy order: command-
permission check (discord.py's own @commands.has_permissions) -> target-hierarchy check
(moderator_can_moderate) -> bot-hierarchy check (bot_can_moderate) ->
execute -> record infraction -> log to the mod-log channel if configured.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.db.models.infraction import InfractionType
from app.metrics import MODERATION_ACTIONS
from app.services.config_service import ConfigurationService
from app.services.moderation_service import (
    ModerationService,
    ModerationValidationError,
    parse_duration,
    validate_clear_amount,
)
from app.utils.permissions import (
    PermissionCheckError,
    bot_can_moderate,
    require_bot_can_moderate,
    require_moderator_can_moderate,
)

logger = logging.getLogger(__name__)

_ESCALATION_ACTION_NAMES: dict[InfractionType, str] = {
    InfractionType.TIMEOUT: "timed out",
    InfractionType.KICK: "kicked",
    InfractionType.BAN: "banned",
}


class ModerationCog(commands.Cog, name="Moderation"):
    """Kick/ban/warn/mute members and manage this channel (prefix commands)."""

    moderation_group = app_commands.Group(
        name="moderation", description="Configure moderation for this server."
    )
    escalation_group = app_commands.Group(
        name="escalation",
        description="Configure automatic action on repeated warnings.",
        parent=moderation_group,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = ModerationService(default_prefix=bot.default_prefix)
        self.config_service = ConfigurationService(default_prefix=bot.default_prefix)

    # --- Shared helpers ---

    async def _can_moderate(self, ctx: commands.Context, target: discord.Member) -> bool:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        try:
            require_moderator_can_moderate(ctx.author, target)
            require_bot_can_moderate(ctx.guild, target)
        except PermissionCheckError as exc:
            await ctx.send(f"⚠️ {exc}")
            return False
        return True

    async def _record_and_log(
        self,
        ctx: commands.Context,
        *,
        target: discord.abc.User,
        type: InfractionType,
        reason: str | None,
        duration_seconds: int | None = None,
    ) -> None:
        assert ctx.guild is not None
        infraction = await self.service.record_infraction(
            ctx.guild.id,
            user_id=target.id,
            moderator_id=ctx.author.id,
            type=type,
            reason=reason,
            duration_seconds=duration_seconds,
        )
        MODERATION_ACTIONS.labels(action=type.value).inc()
        await self._send_mod_log(
            ctx.guild,
            action=type.value,
            target=target,
            moderator=ctx.author,
            reason=infraction.reason or "No reason provided.",
            duration_seconds=duration_seconds,
            infraction_id=infraction.id,
        )

    async def _send_mod_log(
        self,
        guild: discord.Guild,
        *,
        action: str,
        target: discord.abc.User,
        moderator: discord.abc.User,
        reason: str,
        duration_seconds: int | None = None,
        infraction_id: int | None = None,
    ) -> None:
        try:
            config = await self.config_service.get_config(guild.id)
        except Exception:
            logger.exception("Failed to load guild config for mod log", extra={"guild_id": guild.id})
            return
        if config.moderation_log_channel_id is None:
            return

        channel = guild.get_channel(config.moderation_log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Mod-log channel not found or not a text channel", extra={"guild_id": guild.id})
            return

        embed = discord.Embed(title=f"Moderation: {action}", color=discord.Color.orange())
        embed.add_field(name="Target", value=f"{target} ({target.id})", inline=True)
        embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        if duration_seconds is not None:
            embed.add_field(name="Duration", value=str(timedelta(seconds=duration_seconds)), inline=True)
        if infraction_id is not None:
            embed.set_footer(text=f"Infraction #{infraction_id}")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("Failed to send mod log", extra={"guild_id": guild.id})

    async def _apply_escalation(self, ctx: commands.Context, target: discord.Member) -> None:
        """After a !warn, check whether it just crossed a configured threshold and act on it.

        Only checks bot hierarchy, not the original warning moderator's -
        this is a system action, not a re-invocation of !kick/!ban by them.
        """
        assert ctx.guild is not None
        decision = await self.service.check_escalation(ctx.guild.id, target.id)
        if decision.action is None:
            return

        if not bot_can_moderate(ctx.guild, target):
            logger.warning(
                "Escalation triggered but bot can't act on target",
                extra={"guild_id": ctx.guild.id, "user_id": target.id, "action": decision.action.value},
            )
            return

        reason = f"Automatic escalation: {decision.warning_count} active warnings"
        try:
            if decision.action == InfractionType.TIMEOUT:
                await target.timeout(timedelta(hours=1), reason=reason)
                await self._record_and_log(
                    ctx, target=target, type=InfractionType.TIMEOUT, reason=reason, duration_seconds=3600
                )
            elif decision.action == InfractionType.KICK:
                await target.kick(reason=reason)
                await self._record_and_log(ctx, target=target, type=InfractionType.KICK, reason=reason)
            elif decision.action == InfractionType.BAN:
                await target.ban(reason=reason)
                await self._record_and_log(ctx, target=target, type=InfractionType.BAN, reason=reason)
        except discord.HTTPException:
            logger.exception(
                "Failed to execute escalation action",
                extra={"guild_id": ctx.guild.id, "user_id": target.id},
            )
            return

        verb = _ESCALATION_ACTION_NAMES[decision.action]
        await ctx.send(f"⚠️ {target.mention} has been automatically {verb} ({reason}).")

    # --- Prefix commands ---

    @commands.command(name="kick")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def kick(
        self,
        ctx: commands.Context,
        member: discord.Member = commands.parameter(description="The member to kick."),
        *,
        reason: str | None = commands.parameter(
            default=None, description="Why they're being kicked (shown in the mod log)."
        ),
    ) -> None:
        """Kick a member from the server."""
        if not await self._can_moderate(ctx, member):
            return
        try:
            await member.kick(reason=reason or "No reason provided.")
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to kick that member.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't kick that member ({exc}).")
            return

        await self._record_and_log(ctx, target=member, type=InfractionType.KICK, reason=reason)
        await ctx.send(f"✅ Kicked {member}.")

    @commands.command(name="ban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        member: discord.Member = commands.parameter(description="The member to ban."),
        *,
        reason: str | None = commands.parameter(
            default=None, description="Why they're being banned (shown in the mod log)."
        ),
    ) -> None:
        """Ban a member from the server."""
        if not await self._can_moderate(ctx, member):
            return
        try:
            await member.ban(reason=reason or "No reason provided.")
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to ban that member.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't ban that member ({exc}).")
            return

        await self._record_and_log(ctx, target=member, type=InfractionType.BAN, reason=reason)
        await ctx.send(f"✅ Banned {member}.")

    @commands.command(name="unban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def unban(
        self,
        ctx: commands.Context,
        user: discord.User = commands.parameter(description="The banned user's ID or username."),
        *,
        reason: str | None = commands.parameter(default=None, description="Why they're being unbanned."),
    ) -> None:
        """Unban a previously banned user."""
        assert ctx.guild is not None
        try:
            await ctx.guild.unban(user, reason=reason or "No reason provided.")
        except discord.NotFound:
            await ctx.send("⚠️ That user isn't banned.")
            return
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to unban members.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't unban that user ({exc}).")
            return

        resolved = await self.service.resolve_active_bans(ctx.guild.id, user.id)
        MODERATION_ACTIONS.labels(action="unban").inc()
        await self._send_mod_log(
            ctx.guild,
            action="unban",
            target=user,
            moderator=ctx.author,
            reason=reason or "No reason provided.",
        )
        note = f" ({resolved} ban infraction(s) resolved)" if resolved else ""
        await ctx.send(f"✅ Unbanned {user}.{note}")

    @commands.command(name="timeout")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: commands.Context,
        member: discord.Member = commands.parameter(description="The member to time out."),
        duration: str = commands.parameter(
            description="Duration like `10m`, `1h`, `1d`, or `1w`."
        ),
        *,
        reason: str | None = commands.parameter(
            default=None, description="Why they're being timed out (shown in the mod log)."
        ),
    ) -> None:
        """Time out a member so they can't send messages or speak for a set duration."""
        try:
            seconds = parse_duration(duration)
        except ModerationValidationError as exc:
            await ctx.send(f"⚠️ {exc}")
            return
        if not await self._can_moderate(ctx, member):
            return

        try:
            await member.timeout(timedelta(seconds=seconds), reason=reason or "No reason provided.")
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to time out that member.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't time out that member ({exc}).")
            return

        await self._record_and_log(
            ctx, target=member, type=InfractionType.TIMEOUT, reason=reason, duration_seconds=seconds
        )
        await ctx.send(f"✅ Timed out {member} for {timedelta(seconds=seconds)}.")

    @commands.command(name="warn")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def warn(
        self,
        ctx: commands.Context,
        member: discord.Member = commands.parameter(description="The member to warn."),
        *,
        reason: str | None = commands.parameter(default=None, description="Why they're being warned."),
    ) -> None:
        """Warn a member and record it as an infraction. May trigger automatic escalation."""
        if not await self._can_moderate(ctx, member):
            return

        try:
            await self._record_and_log(ctx, target=member, type=InfractionType.WARN, reason=reason)
        except ModerationValidationError as exc:
            await ctx.send(f"⚠️ {exc}")
            return

        await ctx.send(f"✅ Warned {member}.")
        await self._apply_escalation(ctx, member)

    @commands.command(name="warnings")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def warnings(
        self,
        ctx: commands.Context,
        member: discord.Member = commands.parameter(description="The member to look up."),
    ) -> None:
        """List a member's recorded infractions (warnings, kicks, bans, timeouts)."""
        assert ctx.guild is not None
        infractions = await self.service.list_infractions(ctx.guild.id, member.id)
        if not infractions:
            await ctx.send(f"{member} has no recorded infractions.")
            return

        lines = []
        for infraction in infractions[:25]:
            status = "active" if infraction.active else "resolved"
            reason = infraction.reason or "No reason provided."
            lines.append(
                f"`#{infraction.id}` **{infraction.type.value}** ({status}) - {reason} "
                f"- <t:{int(infraction.created_at.timestamp())}:R>"
            )

        embed = discord.Embed(
            title=f"Infractions for {member}", description="\n".join(lines), color=discord.Color.orange()
        )
        await ctx.send(embed=embed)

    @commands.command(name="clear")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def clear(
        self,
        ctx: commands.Context,
        amount: int = commands.parameter(description="Number of recent messages to delete (1-100)."),
    ) -> None:
        """Bulk-delete recent messages in this channel."""
        assert isinstance(ctx.channel, discord.TextChannel)
        try:
            amount = validate_clear_amount(amount)
        except ModerationValidationError as exc:
            await ctx.send(f"⚠️ {exc}")
            return

        try:
            deleted = await ctx.channel.purge(limit=amount)
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to delete messages here.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't delete those messages ({exc}).")
            return

        MODERATION_ACTIONS.labels(action="clear").inc()
        await ctx.send(f"✅ Deleted {len(deleted)} message(s).", delete_after=5)

    @commands.command(name="slowmode")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def slowmode(
        self,
        ctx: commands.Context,
        seconds: int = commands.parameter(
            description="Delay between messages, in seconds (0-21600). 0 disables it."
        ),
    ) -> None:
        """Set this channel's slowmode delay."""
        assert isinstance(ctx.channel, discord.TextChannel)
        if not 0 <= seconds <= 21600:
            await ctx.send("⚠️ Slowmode must be between 0 and 21600 seconds (6 hours).")
            return

        try:
            await ctx.channel.edit(slowmode_delay=seconds)
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to edit this channel.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't update slowmode ({exc}).")
            return

        MODERATION_ACTIONS.labels(action="slowmode").inc()
        value = "disabled" if seconds == 0 else f"set to {seconds}s"
        await ctx.send(f"✅ Slowmode {value}.")

    @commands.command(name="lock")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context) -> None:
        """Prevent @everyone from sending messages in this channel."""
        assert ctx.guild is not None and isinstance(ctx.channel, discord.TextChannel)
        try:
            await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to edit this channel.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't lock this channel ({exc}).")
            return

        MODERATION_ACTIONS.labels(action="lock").inc()
        await ctx.send("🔒 Channel locked.")

    @commands.command(name="unlock")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context) -> None:
        """Restore @everyone's ability to send messages in this channel."""
        assert ctx.guild is not None and isinstance(ctx.channel, discord.TextChannel)
        try:
            await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to edit this channel.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"⚠️ Couldn't unlock this channel ({exc}).")
            return

        MODERATION_ACTIONS.labels(action="unlock").inc()
        await ctx.send("🔓 Channel unlocked.")

    # --- /moderation escalation commands ---

    @escalation_group.command(name="enable", description="Enable automatic escalation on repeated warnings.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def escalation_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.service.set_escalation_enabled(interaction.guild.id, True)
        await interaction.response.send_message("✅ Escalation enabled.", ephemeral=True)

    @escalation_group.command(name="disable", description="Disable automatic escalation on repeated warnings.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def escalation_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.service.set_escalation_enabled(interaction.guild.id, False)
        await interaction.response.send_message("✅ Escalation disabled.", ephemeral=True)

    @escalation_group.command(name="set", description="Set the action taken at a given warning count.")
    @app_commands.describe(warnings="Warning count that triggers the action.", action="Action to take.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def escalation_set(
        self, interaction: discord.Interaction, warnings: int, action: Literal["timeout", "kick", "ban"]
    ) -> None:
        assert interaction.guild is not None
        try:
            await self.service.set_escalation_threshold(
                interaction.guild.id, warnings, InfractionType(action)
            )
        except ModerationValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ At {warnings} active warning(s), members will be {action}ed.", ephemeral=True
        )

    @escalation_group.command(name="clear", description="Remove the escalation action at a given warning count.")
    @app_commands.describe(warnings="Warning count to clear the configured action for.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def escalation_clear(self, interaction: discord.Interaction, warnings: int) -> None:
        assert interaction.guild is not None
        await self.service.clear_escalation_threshold(interaction.guild.id, warnings)
        await interaction.response.send_message(f"✅ Cleared the threshold at {warnings} warning(s).", ephemeral=True)

    @escalation_group.command(name="show", description="Show the current escalation configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def escalation_show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.service.get_config(interaction.guild.id)

        embed = discord.Embed(title="Moderation Escalation", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value="Yes" if config.escalation_enabled else "No", inline=True)
        if config.escalation_thresholds:
            lines = [
                f"{count} warning(s) → {action}"
                for count, action in sorted(config.escalation_thresholds.items(), key=lambda kv: int(kv[0]))
            ]
            embed.add_field(name="Thresholds", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Thresholds", value="None configured", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
