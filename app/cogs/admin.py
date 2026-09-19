"""Core administrative configuration commands.

This is the first fully-wired vertical slice (Discord -> service ->
repository -> SQLite) and exists partly as a template for the other cogs
added in later implementation steps (welcome, roles, moderation, custom
commands, music).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.services.config_service import ConfigurationService, ConfigValidationError


class AdminCog(commands.Cog, name="Admin"):
    config_group = app_commands.Group(
        name="config", description="Core C3P0 configuration for this server."
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config_service = ConfigurationService(default_prefix=bot.default_prefix)

    def _invalidate_prefix_cache(self, guild_id: int) -> None:
        self.bot.prefix_cache.pop(guild_id, None)

    @config_group.command(name="show", description="Show C3P0's current configuration for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.config_service.get_config(interaction.guild.id)

        embed = discord.Embed(title="C3P0 Configuration", color=discord.Color.blurple())
        embed.add_field(name="Prefix", value=f"`{config.prefix}`", inline=True)
        embed.add_field(
            name="Default role",
            value=f"<@&{config.default_role_id}>" if config.default_role_id else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Log channel",
            value=f"<#{config.log_channel_id}>" if config.log_channel_id else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Mod log channel",
            value=f"<#{config.moderation_log_channel_id}>" if config.moderation_log_channel_id else "Not set",
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config_group.command(name="prefix", description="Set the command prefix for this server.")
    @app_commands.describe(prefix="The new prefix, e.g. ! or ?")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_prefix(self, interaction: discord.Interaction, prefix: str) -> None:
        assert interaction.guild is not None
        try:
            config = await self.config_service.set_prefix(interaction.guild.id, prefix)
        except ConfigValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        self._invalidate_prefix_cache(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Prefix updated to `{config.prefix}`.", ephemeral=True
        )

    @config_group.command(
        name="default-role", description="Set the role automatically assigned to new members."
    )
    @app_commands.describe(role="The role to assign on join, or omit to clear it.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_default_role(
        self, interaction: discord.Interaction, role: discord.Role | None = None
    ) -> None:
        assert interaction.guild is not None

        if role is not None:
            me = interaction.guild.me
            if me is None or me.top_role <= role:
                await interaction.response.send_message(
                    "⚠️ I can't assign that role - it's above my highest role.", ephemeral=True
                )
                return

        await self.config_service.set_default_role(interaction.guild.id, role.id if role else None)
        value = role.mention if role else "cleared"
        await interaction.response.send_message(f"✅ Default role {value}.", ephemeral=True)

    @config_group.command(
        name="log-channel", description="Set the channel for general bot activity logs."
    )
    @app_commands.describe(channel="The channel to log to, or omit to clear it.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        assert interaction.guild is not None
        await self.config_service.set_log_channel(interaction.guild.id, channel.id if channel else None)
        value = channel.mention if channel else "cleared"
        await interaction.response.send_message(f"✅ Log channel {value}.", ephemeral=True)

    @config_group.command(
        name="mod-log-channel", description="Set the channel moderation actions are logged to."
    )
    @app_commands.describe(channel="The channel to log moderation actions to, or omit to clear it.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_mod_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        assert interaction.guild is not None
        await self.config_service.set_moderation_log_channel(
            interaction.guild.id, channel.id if channel else None
        )
        value = channel.mention if channel else "cleared"
        await interaction.response.send_message(f"✅ Moderation log channel {value}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
