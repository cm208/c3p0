"""Welcome/member-join cog.

Handles the on_member_join/on_member_remove listeners and the /welcome
configuration command group. Failure behavior throughout is log and
continue - never let a missing channel, a blocked DM, or an unassignable
role stop the rest of the join from being processed.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.metrics import MEMBER_JOINS, MEMBER_LEAVES
from app.services.config_service import ConfigurationService
from app.services.welcome_service import (
    DEFAULT_DM_TEMPLATE,
    DEFAULT_MESSAGE_TEMPLATE,
    JoinContext,
    WelcomeConfigView,
    WelcomeService,
    WelcomeValidationError,
)
from app.utils.permissions import (
    PermissionCheckError,
    bot_can_manage_role,
    require_bot_can_manage_role,
)

logger = logging.getLogger(__name__)

_EMBED_FIELD_PREVIEW_LIMIT = 990


def _build_context(member: discord.Member, channel: discord.TextChannel | None) -> JoinContext:
    return JoinContext(
        user_display_name=str(member),
        user_mention=member.mention,
        user_id=member.id,
        guild_name=member.guild.name,
        member_count=member.guild.member_count or 0,
        channel_name=channel.name if channel else "",
        channel_mention=channel.mention if channel else "",
    )


def _preview(text: str, limit: int = _EMBED_FIELD_PREVIEW_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class WelcomeCog(commands.Cog, name="Welcome"):
    welcome_group = app_commands.Group(
        name="welcome", description="Configure C3P0's member-welcome system."
    )
    dm_group = app_commands.Group(
        name="dm", description="Configure the welcome direct message.", parent=welcome_group
    )
    join_log_group = app_commands.Group(
        name="join-log",
        description="Configure join logging to this server's configured log channel.",
        parent=welcome_group,
    )
    embed_group = app_commands.Group(
        name="embed", description="Configure the welcome message embed.", parent=welcome_group
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.welcome_service = WelcomeService(default_prefix=bot.default_prefix)
        self.config_service = ConfigurationService(default_prefix=bot.default_prefix)

    # --- Listeners ---

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        MEMBER_JOINS.inc()
        guild = member.guild

        try:
            config = await self.welcome_service.get_config(guild.id)
        except Exception:
            logger.exception("Failed to load welcome config on member join", extra={"guild_id": guild.id})
            return

        if not config.enabled:
            return

        channel: discord.TextChannel | None = None
        if config.channel_id is not None:
            resolved = guild.get_channel(config.channel_id)
            if isinstance(resolved, discord.TextChannel):
                channel = resolved
            else:
                logger.warning(
                    "Welcome channel not found or not a text channel",
                    extra={"guild_id": guild.id, "channel_id": config.channel_id},
                )

        if config.message_enabled and channel is not None:
            await self._send_channel_message(config, member, channel)

        if config.dm_enabled:
            await self._send_dm(config, member)

        if config.role_enabled and config.role_id is not None:
            await self._assign_role(config, member)

        if config.join_log_enabled:
            await self._send_join_log(guild, member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        MEMBER_LEAVES.inc()

    # --- Join-handling helpers ---

    async def _send_channel_message(
        self, config: WelcomeConfigView, member: discord.Member, channel: discord.TextChannel
    ) -> None:
        context = _build_context(member, channel)
        template = config.message_template or DEFAULT_MESSAGE_TEMPLATE
        try:
            if config.embed_enabled:
                title = (
                    self.welcome_service.render_join_message(config.embed_title, context)
                    if config.embed_title
                    else None
                )
                description = self.welcome_service.render_join_message(
                    config.embed_description or template, context
                )
                footer = (
                    self.welcome_service.render_join_message(config.embed_footer, context)
                    if config.embed_footer
                    else None
                )
                embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
                if footer:
                    embed.set_footer(text=footer)
                await channel.send(embed=embed)
            else:
                await channel.send(self.welcome_service.render_join_message(template, context))
        except discord.Forbidden:
            logger.warning(
                "Missing permission to send welcome message",
                extra={"guild_id": member.guild.id, "channel_id": channel.id},
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to send welcome message",
                extra={"guild_id": member.guild.id, "channel_id": channel.id},
            )

    async def _send_dm(self, config: WelcomeConfigView, member: discord.Member) -> None:
        context = _build_context(member, None)
        template = config.dm_template or DEFAULT_DM_TEMPLATE
        try:
            await member.send(self.welcome_service.render_join_message(template, context))
        except discord.Forbidden:
            # Expected whenever the member has DMs closed - not a bot failure.
            logger.info(
                "Could not DM new member - DMs likely closed",
                extra={"guild_id": member.guild.id, "user_id": member.id},
            )
        except discord.HTTPException:
            logger.exception("Failed to DM new member", extra={"guild_id": member.guild.id})

    async def _assign_role(self, config: WelcomeConfigView, member: discord.Member) -> None:
        guild = member.guild
        role = guild.get_role(config.role_id) if config.role_id is not None else None
        if role is None:
            logger.warning(
                "Configured welcome role not found",
                extra={"guild_id": guild.id, "role_id": config.role_id},
            )
            return
        if not bot_can_manage_role(guild, role):
            logger.warning(
                "Cannot assign welcome role - positioned above the bot's highest role",
                extra={"guild_id": guild.id, "role_id": role.id},
            )
            return
        try:
            await member.add_roles(role, reason="C3P0 welcome auto-role")
        except discord.Forbidden:
            logger.warning(
                "Missing permission to assign welcome role",
                extra={"guild_id": guild.id, "role_id": role.id},
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to assign welcome role", extra={"guild_id": guild.id, "role_id": role.id}
            )

    async def _send_join_log(self, guild: discord.Guild, member: discord.Member) -> None:
        try:
            guild_config = await self.config_service.get_config(guild.id)
        except Exception:
            logger.exception("Failed to load guild config for join log", extra={"guild_id": guild.id})
            return

        if guild_config.log_channel_id is None:
            return

        channel = guild.get_channel(guild_config.log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Join-log channel not found or not a text channel", extra={"guild_id": guild.id})
            return

        try:
            await channel.send(f"➡️ {member} (`{member.id}`) joined the server.")
        except discord.Forbidden:
            logger.warning("Missing permission to send join log", extra={"guild_id": guild.id})
        except discord.HTTPException:
            logger.exception("Failed to send join log", extra={"guild_id": guild.id})

    # --- /welcome commands ---

    @welcome_group.command(name="enable", description="Enable the welcome system for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_enabled(interaction.guild.id, True)
        await interaction.response.send_message("✅ Welcome system enabled.", ephemeral=True)

    @welcome_group.command(name="disable", description="Disable the welcome system for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_enabled(interaction.guild.id, False)
        await interaction.response.send_message("✅ Welcome system disabled.", ephemeral=True)

    @welcome_group.command(name="channel", description="Set the channel welcome messages are posted to.")
    @app_commands.describe(channel="The channel to post welcome messages in, or omit to clear it.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_channel(interaction.guild.id, channel.id if channel else None)
        value = channel.mention if channel else "cleared"
        await interaction.response.send_message(f"✅ Welcome channel {value}.", ephemeral=True)

    @welcome_group.command(name="message", description="Set the welcome message template.")
    @app_commands.describe(
        text="Supports {user}, {user_mention}, {user_id}, {server}, {member_count}, {channel}, {channel_mention}."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_message(self, interaction: discord.Interaction, text: str) -> None:
        assert interaction.guild is not None
        try:
            await self.welcome_service.set_message(interaction.guild.id, text)
        except WelcomeValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.send_message("✅ Welcome message updated.", ephemeral=True)

    @dm_group.command(name="enable", description="Enable sending a DM to new members.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_dm_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_dm_enabled(interaction.guild.id, True)
        await interaction.response.send_message("✅ Welcome DM enabled.", ephemeral=True)

    @dm_group.command(name="disable", description="Disable sending a DM to new members.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_dm_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_dm_enabled(interaction.guild.id, False)
        await interaction.response.send_message("✅ Welcome DM disabled.", ephemeral=True)

    @welcome_group.command(name="dm-message", description="Set the welcome DM template.")
    @app_commands.describe(text="Supports the same variables as the welcome message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_dm_message(self, interaction: discord.Interaction, text: str) -> None:
        assert interaction.guild is not None
        try:
            await self.welcome_service.set_dm_message(interaction.guild.id, text)
        except WelcomeValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.send_message("✅ Welcome DM message updated.", ephemeral=True)

    @welcome_group.command(name="role", description="Set the role automatically assigned to new members.")
    @app_commands.describe(role="The role to assign on join.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await self.welcome_service.set_role(interaction.guild.id, role.id)
        await interaction.response.send_message(f"✅ Welcome role set to {role.mention}.", ephemeral=True)

    @welcome_group.command(name="role-disable", description="Disable auto-role assignment on join.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_role_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.disable_role(interaction.guild.id)
        await interaction.response.send_message("✅ Welcome auto-role disabled.", ephemeral=True)

    @embed_group.command(
        name="enable", description="Post the welcome message as an embed instead of plain text."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_embed_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_embed_enabled(interaction.guild.id, True)
        await interaction.response.send_message("✅ Welcome embed enabled.", ephemeral=True)

    @embed_group.command(name="disable", description="Post the welcome message as plain text.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_embed_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_embed_enabled(interaction.guild.id, False)
        await interaction.response.send_message("✅ Welcome embed disabled.", ephemeral=True)

    @embed_group.command(name="title", description="Set the welcome embed title.")
    @app_commands.describe(text="Embed title, or omit to clear it.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_embed_title(self, interaction: discord.Interaction, text: str | None = None) -> None:
        assert interaction.guild is not None
        try:
            await self.welcome_service.set_embed_title(interaction.guild.id, text)
        except WelcomeValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        value = "cleared" if text is None else "updated"
        await interaction.response.send_message(f"✅ Embed title {value}.", ephemeral=True)

    @embed_group.command(name="description", description="Set the welcome embed description.")
    @app_commands.describe(
        text="Embed description, or omit to clear it (falls back to the welcome message)."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_embed_description(
        self, interaction: discord.Interaction, text: str | None = None
    ) -> None:
        assert interaction.guild is not None
        try:
            await self.welcome_service.set_embed_description(interaction.guild.id, text)
        except WelcomeValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        value = "cleared" if text is None else "updated"
        await interaction.response.send_message(f"✅ Embed description {value}.", ephemeral=True)

    @embed_group.command(name="footer", description="Set the welcome embed footer.")
    @app_commands.describe(text="Embed footer text, or omit to clear it.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_embed_footer(self, interaction: discord.Interaction, text: str | None = None) -> None:
        assert interaction.guild is not None
        try:
            await self.welcome_service.set_embed_footer(interaction.guild.id, text)
        except WelcomeValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        value = "cleared" if text is None else "updated"
        await interaction.response.send_message(f"✅ Embed footer {value}.", ephemeral=True)

    @join_log_group.command(
        name="enable", description="Log new-member joins to this server's configured log channel."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_join_log_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        guild_config = await self.config_service.get_config(interaction.guild.id)
        if guild_config.log_channel_id is None:
            await interaction.response.send_message(
                "⚠️ Set a log channel first with `/config log-channel` before enabling join logging.",
                ephemeral=True,
            )
            return
        await self.welcome_service.set_join_log_enabled(interaction.guild.id, True)
        await interaction.response.send_message("✅ Join logging enabled.", ephemeral=True)

    @join_log_group.command(name="disable", description="Stop logging new-member joins.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_join_log_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.set_join_log_enabled(interaction.guild.id, False)
        await interaction.response.send_message("✅ Join logging disabled.", ephemeral=True)

    @welcome_group.command(name="show", description="Show the current welcome configuration for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.welcome_service.get_config(interaction.guild.id)

        embed = discord.Embed(title="Welcome Configuration", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value="Yes" if config.enabled else "No", inline=True)
        embed.add_field(
            name="Channel",
            value=f"<#{config.channel_id}>" if config.channel_id else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Message",
            value=(
                f"```{_preview(config.message_template)}```"
                if config.message_template
                else f"_default: {DEFAULT_MESSAGE_TEMPLATE}_"
            ),
            inline=False,
        )
        embed.add_field(name="Embed mode", value="Yes" if config.embed_enabled else "No", inline=True)
        embed.add_field(name="DM enabled", value="Yes" if config.dm_enabled else "No", inline=True)
        embed.add_field(
            name="DM message",
            value=(
                f"```{_preview(config.dm_template)}```"
                if config.dm_template
                else f"_default: {DEFAULT_DM_TEMPLATE}_"
            ),
            inline=False,
        )
        embed.add_field(
            name="Auto-role",
            value=f"<@&{config.role_id}>" if config.role_enabled and config.role_id else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Join logging", value="Yes" if config.join_log_enabled else "No", inline=True
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(
        name="reset", description="Reset all welcome configuration for this server to defaults."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_reset(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.welcome_service.reset(interaction.guild.id)
        await interaction.response.send_message("✅ Welcome configuration reset to defaults.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
