"""Reaction/button/select self-assignable role cog - all of step 8.

Handles raw reaction listeners, a generic component (button/select)
interaction listener, and the /reactionrole, /rolebutton, /roleselect
command groups.

Raw (not cached) reaction events are used deliberately: a reaction-role
message posted before the bot's last restart, or on a message outside the
message cache, must still work - on_reaction_add/remove only fire for
cached messages.

Buttons/selects deliberately do NOT use discord.py's persistent-View
registration (`bot.add_view()` at startup). That mechanism is built for a
small number of *statically known* view layouts. Ours are fully dynamic -
an arbitrary number of buttons/options per message, driven entirely by
whatever RoleBinding rows exist - so instead this listens to the raw
`on_interaction` event directly and dispatches by `custom_id` prefix,
exactly like the raw reaction handling above. This works because
discord.py's gateway parser (see discord/state.py's parse_interaction_create)
unconditionally calls `self.dispatch('interaction', ...)` for every
interaction *in addition to* routing slash commands through the command
tree and registered views through its own view store - our handler simply
never touches that view store, so there's nothing to keep in sync across
restarts. The View objects built below (_build_button_view/
_build_select_view) exist only to tell Discord what to render when we
send/edit a message; they carry no callbacks of their own.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

from app.db.models.role_binding import (
    BUTTON_CUSTOM_ID_PREFIX,
    SELECT_CUSTOM_ID_PREFIX,
    RoleBindingType,
    select_custom_id,
)
from app.services.role_binding_service import (
    RoleBindingService,
    RoleBindingValidationError,
    RoleBindingView,
)
from app.utils.discord_links import InvalidMessageLinkError, parse_message_link
from app.utils.emoji import InvalidEmojiError, normalize_emoji
from app.utils.permissions import (
    PermissionCheckError,
    bot_can_manage_role,
    require_bot_can_manage_role,
)

logger = logging.getLogger(__name__)

_LIST_DESCRIPTION_LIMIT = 3900
_MAX_COMPONENTS_PER_MESSAGE = 25  # Discord's limit for both buttons-per-view and options-per-select


class RolesCog(commands.Cog, name="Roles"):
    reactionrole_group = app_commands.Group(
        name="reactionrole", description="Configure reaction-based self-assignable roles."
    )
    rolebutton_group = app_commands.Group(
        name="rolebutton", description="Configure button-based self-assignable roles."
    )
    roleselect_group = app_commands.Group(
        name="roleselect", description="Configure select-menu-based self-assignable roles."
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.role_service = RoleBindingService(default_prefix=bot.default_prefix)

    # --- Listeners ---

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, adding=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, *, adding: bool) -> None:
        if payload.guild_id is None:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        emoji = str(payload.emoji)
        try:
            binding = await self.role_service.find_reaction_binding(guild.id, payload.message_id, emoji)
        except Exception:
            logger.exception("Failed to look up reaction-role binding", extra={"guild_id": guild.id})
            return

        if binding is None or not binding.enabled:
            return
        if not adding and not binding.toggle:
            # This binding only grants on add; removing the reaction is a no-op by design.
            return

        role = guild.get_role(binding.role_id)
        if role is None:
            logger.warning(
                "Reaction-role role no longer exists",
                extra={"guild_id": guild.id, "role_id": binding.role_id},
            )
            return
        if not bot_can_manage_role(guild, role):
            logger.warning(
                "Cannot manage reaction-role - positioned above the bot's highest role",
                extra={"guild_id": guild.id, "role_id": role.id},
            )
            return

        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                return
            except discord.HTTPException:
                logger.exception("Failed to fetch member for reaction role", extra={"guild_id": guild.id})
                return

        try:
            if adding:
                await member.add_roles(role, reason="C3P0 reaction role")
            else:
                await member.remove_roles(role, reason="C3P0 reaction role")
        except discord.Forbidden:
            logger.warning(
                "Missing permission to update reaction-role",
                extra={"guild_id": guild.id, "role_id": role.id},
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to update reaction-role", extra={"guild_id": guild.id, "role_id": role.id}
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component or interaction.data is None:
            return
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith(BUTTON_CUSTOM_ID_PREFIX):
            await self._handle_button_interaction(interaction, custom_id)
        elif custom_id.startswith(SELECT_CUSTOM_ID_PREFIX):
            await self._handle_select_interaction(interaction)

    async def _respond_component_error(self, interaction: discord.Interaction, message: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"⚠️ {message}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {message}", ephemeral=True)
        except discord.HTTPException:
            logger.exception("Failed to send component-interaction error response")

    async def _handle_button_interaction(self, interaction: discord.Interaction, custom_id: str) -> None:
        guild = interaction.guild
        if guild is None or interaction.message is None or not isinstance(interaction.user, discord.Member):
            return
        member = interaction.user

        try:
            binding = await self.role_service.find_button_binding(guild.id, interaction.message.id, custom_id)
        except Exception:
            logger.exception("Failed to look up button-role binding", extra={"guild_id": guild.id})
            await self._respond_component_error(interaction, "Something went wrong.")
            return

        if binding is None or not binding.enabled:
            await self._respond_component_error(interaction, "This button isn't configured anymore.")
            return

        role = guild.get_role(binding.role_id)
        if role is None:
            await self._respond_component_error(interaction, "That role no longer exists.")
            return
        if not bot_can_manage_role(guild, role):
            await self._respond_component_error(interaction, "I can't manage that role anymore.")
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="C3P0 role button")
                await interaction.response.send_message(f"➖ Removed {role.mention}.", ephemeral=True)
            else:
                await member.add_roles(role, reason="C3P0 role button")
                await interaction.response.send_message(f"➕ Added {role.mention}.", ephemeral=True)
        except discord.Forbidden:
            await self._respond_component_error(interaction, "I don't have permission to manage that role.")
        except discord.HTTPException:
            logger.exception(
                "Failed to toggle button role", extra={"guild_id": guild.id, "role_id": role.id}
            )
            await self._respond_component_error(interaction, "Something went wrong updating your roles.")

    async def _handle_select_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or interaction.message is None or not isinstance(interaction.user, discord.Member):
            return
        member = interaction.user

        raw_values = (interaction.data or {}).get("values", [])
        try:
            selected_ids = {int(v) for v in raw_values}
        except (TypeError, ValueError):
            logger.warning("Received non-integer select values", extra={"guild_id": guild.id})
            return

        try:
            bindings = await self.role_service.list_for_message(
                guild.id, interaction.message.id, RoleBindingType.SELECT
            )
        except Exception:
            logger.exception("Failed to look up select-role bindings", extra={"guild_id": guild.id})
            await self._respond_component_error(interaction, "Something went wrong.")
            return

        enabled = [b for b in bindings if b.enabled]
        if not enabled:
            await self._respond_component_error(interaction, "This menu isn't configured anymore.")
            return

        granted: list[str] = []
        revoked: list[str] = []
        for binding in enabled:
            role = guild.get_role(binding.role_id)
            if role is None or not bot_can_manage_role(guild, role):
                continue
            should_have = binding.id in selected_ids
            has_it = role in member.roles
            if should_have == has_it:
                continue
            try:
                if should_have:
                    await member.add_roles(role, reason="C3P0 role select")
                    granted.append(role.mention)
                else:
                    await member.remove_roles(role, reason="C3P0 role select")
                    revoked.append(role.mention)
            except discord.Forbidden:
                logger.warning(
                    "Missing permission to update select role",
                    extra={"guild_id": guild.id, "role_id": role.id},
                )
            except discord.HTTPException:
                logger.exception(
                    "Failed to update select role", extra={"guild_id": guild.id, "role_id": role.id}
                )

        parts = []
        if granted:
            parts.append("Added " + ", ".join(granted))
        if revoked:
            parts.append("Removed " + ", ".join(revoked))
        await interaction.response.send_message(" / ".join(parts) or "No changes.", ephemeral=True)

    # --- View builders (rendering only - no callbacks; see module docstring) ---

    def _build_button_view(self, guild: discord.Guild, bindings: list[RoleBindingView]) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        for binding in bindings:
            role = guild.get_role(binding.role_id)
            label = role.name if role is not None else f"role {binding.role_id}"
            view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    label=label,
                    custom_id=binding.component_custom_id,
                    emoji=binding.emoji,
                )
            )
        return view

    def _build_select_view(
        self, guild: discord.Guild, message_id: int, bindings: list[RoleBindingView], placeholder: str
    ) -> discord.ui.View:
        options = []
        for binding in bindings:
            role = guild.get_role(binding.role_id)
            label = role.name if role is not None else f"role {binding.role_id}"
            options.append(discord.SelectOption(label=label, value=binding.component_custom_id))
        select = discord.ui.Select(
            custom_id=select_custom_id(message_id),
            placeholder=placeholder,
            min_values=0,
            max_values=len(options),
            options=options,
        )
        view = discord.ui.View(timeout=None)
        view.add_item(select)
        return view

    # --- Helpers shared by multiple commands ---

    async def _resolve_existing_message(
        self, interaction: discord.Interaction, message_link: str
    ) -> tuple[discord.TextChannel, discord.Message] | None:
        assert interaction.guild is not None
        try:
            guild_id, channel_id, message_id = parse_message_link(message_link)
        except InvalidMessageLinkError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return None
        if guild_id != interaction.guild.id:
            await interaction.response.send_message("⚠️ That message isn't in this server.", ephemeral=True)
            return None

        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("⚠️ Couldn't find that channel.", ephemeral=True)
            return None

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.response.send_message("⚠️ Couldn't find that message.", ephemeral=True)
            return None
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't fetch that message ({exc}).", ephemeral=True)
            return None
        return channel, message

    # --- /reactionrole commands ---

    @reactionrole_group.command(
        name="create", description="Post a new message and bind an emoji reaction to a role on it."
    )
    @app_commands.describe(
        channel="Channel to post the message in.",
        emoji="The emoji members react with (unicode or a custom server emoji).",
        role="The role to grant.",
        message="The message text to post.",
        toggle="Whether removing the reaction also removes the role (default: yes).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        emoji: str,
        role: discord.Role,
        message: str = "React below to get a role!",
        toggle: bool = True,
    ) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        try:
            normalized_emoji = normalize_emoji(emoji)
        except InvalidEmojiError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        try:
            posted = await channel.send(message)
            await posted.add_reaction(normalized_emoji)
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"⚠️ Couldn't post that message or add that reaction ({exc}).", ephemeral=True
            )
            return

        try:
            await self.role_service.create_reaction_binding(
                interaction.guild.id,
                channel_id=channel.id,
                message_id=posted.id,
                emoji=normalized_emoji,
                role_id=role.id,
                toggle=toggle,
            )
        except RoleBindingValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Reaction role created in {channel.mention}: {normalized_emoji} → {role.mention}.",
            ephemeral=True,
        )

    @reactionrole_group.command(
        name="add", description="Bind an emoji reaction to a role on an existing message."
    )
    @app_commands.describe(
        message_link="Link to the message (right-click it, then 'Copy Message Link').",
        emoji="The emoji members react with.",
        role="The role to grant.",
        toggle="Whether removing the reaction also removes the role (default: yes).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_add(
        self,
        interaction: discord.Interaction,
        message_link: str,
        emoji: str,
        role: discord.Role,
        toggle: bool = True,
    ) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        channel, message = resolved

        try:
            normalized_emoji = normalize_emoji(emoji)
        except InvalidEmojiError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        try:
            await message.add_reaction(normalized_emoji)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't add that reaction ({exc}).", ephemeral=True)
            return

        try:
            await self.role_service.create_reaction_binding(
                interaction.guild.id,
                channel_id=channel.id,
                message_id=message.id,
                emoji=normalized_emoji,
                role_id=role.id,
                toggle=toggle,
            )
        except RoleBindingValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Bound {normalized_emoji} → {role.mention} on that message.", ephemeral=True
        )

    @reactionrole_group.command(name="remove", description="Remove one emoji/role mapping from a message.")
    @app_commands.describe(message_link="Link to the message.", emoji="The emoji to unbind.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_remove(
        self, interaction: discord.Interaction, message_link: str, emoji: str
    ) -> None:
        assert interaction.guild is not None
        try:
            guild_id, channel_id, message_id = parse_message_link(message_link)
        except InvalidMessageLinkError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        if guild_id != interaction.guild.id:
            await interaction.response.send_message("⚠️ That message isn't in this server.", ephemeral=True)
            return

        try:
            normalized_emoji = normalize_emoji(emoji)
        except InvalidEmojiError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        removed = await self.role_service.remove_reaction_binding(
            interaction.guild.id, message_id, normalized_emoji
        )
        if not removed:
            await interaction.response.send_message(
                "⚠️ No mapping found for that emoji on that message.", ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(message_id)
                await message.clear_reaction(normalized_emoji)
            except discord.HTTPException:
                pass  # best-effort cosmetic cleanup - the binding is already gone

        await interaction.response.send_message(f"✅ Removed the {normalized_emoji} mapping.", ephemeral=True)

    @reactionrole_group.command(
        name="delete", description="Remove every reaction-role mapping from a message."
    )
    @app_commands.describe(message_link="Link to the message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_delete(self, interaction: discord.Interaction, message_link: str) -> None:
        assert interaction.guild is not None
        try:
            guild_id, _channel_id, message_id = parse_message_link(message_link)
        except InvalidMessageLinkError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        if guild_id != interaction.guild.id:
            await interaction.response.send_message("⚠️ That message isn't in this server.", ephemeral=True)
            return

        count = await self.role_service.delete_message_bindings_by_type(
            interaction.guild.id, message_id, RoleBindingType.REACTION
        )
        await interaction.response.send_message(
            f"✅ Removed {count} mapping(s) from that message.", ephemeral=True
        )

    @reactionrole_group.command(name="enable", description="Enable all reaction-role mappings on a message.")
    @app_commands.describe(message_link="Link to the message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_enable(self, interaction: discord.Interaction, message_link: str) -> None:
        await self._set_message_enabled(interaction, message_link, True)

    @reactionrole_group.command(
        name="disable", description="Disable all reaction-role mappings on a message."
    )
    @app_commands.describe(message_link="Link to the message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_disable(self, interaction: discord.Interaction, message_link: str) -> None:
        await self._set_message_enabled(interaction, message_link, False)

    async def _set_message_enabled(
        self, interaction: discord.Interaction, message_link: str, enabled: bool
    ) -> None:
        assert interaction.guild is not None
        try:
            guild_id, _channel_id, message_id = parse_message_link(message_link)
        except InvalidMessageLinkError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        if guild_id != interaction.guild.id:
            await interaction.response.send_message("⚠️ That message isn't in this server.", ephemeral=True)
            return

        count = await self.role_service.set_message_enabled(interaction.guild.id, message_id, enabled)
        if count == 0:
            await interaction.response.send_message("⚠️ No mappings found for that message.", ephemeral=True)
            return
        state = "Enabled" if enabled else "Disabled"
        await interaction.response.send_message(f"✅ {state} {count} mapping(s).", ephemeral=True)

    @reactionrole_group.command(name="list", description="List all reaction-role mappings in this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        bindings = await self.role_service.list_bindings(interaction.guild.id, RoleBindingType.REACTION)
        if not bindings:
            await interaction.response.send_message("No reaction-role mappings configured.", ephemeral=True)
            return

        lines = []
        for binding in bindings:
            status = "✅" if binding.enabled else "⏸️"
            link = (
                f"https://discord.com/channels/{interaction.guild.id}/"
                f"{binding.source_channel_id}/{binding.source_message_id}"
            )
            lines.append(f"{status} {binding.emoji} → <@&{binding.role_id}> - [message]({link})")

        description = "\n".join(lines)
        if len(description) > _LIST_DESCRIPTION_LIMIT:
            description = description[:_LIST_DESCRIPTION_LIMIT] + "\n…and more"

        embed = discord.Embed(title="Reaction Roles", description=description, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reactionrole_group.command(
        name="diagnose",
        description="Check reaction-role mappings for deleted channels, roles, or messages.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactionrole_diagnose(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        bindings = await self.role_service.list_bindings(guild.id)
        if not bindings:
            await interaction.followup.send("No role mappings configured.", ephemeral=True)
            return

        problems: list[str] = []
        message_ok_cache: dict[int, bool] = {}
        for binding in bindings:
            label = f"{binding.interaction_type.value} mapping (id {binding.id})"

            if guild.get_role(binding.role_id) is None:
                problems.append(f"{label}: role no longer exists.")
                continue

            channel = guild.get_channel(binding.source_channel_id)
            if channel is None:
                problems.append(f"{label}: channel no longer exists.")
                continue

            if binding.source_message_id not in message_ok_cache:
                try:
                    await channel.fetch_message(binding.source_message_id)
                    message_ok_cache[binding.source_message_id] = True
                except discord.NotFound:
                    message_ok_cache[binding.source_message_id] = False
                except discord.HTTPException:
                    # Unknown (rate-limited, permissions, etc.) - don't flag a false positive.
                    message_ok_cache[binding.source_message_id] = True

            if not message_ok_cache[binding.source_message_id]:
                problems.append(f"{label}: message no longer exists.")

        if not problems:
            await interaction.followup.send("✅ No stale mappings found.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Stale mappings:\n" + "\n".join(problems), ephemeral=True)

    # --- /rolebutton commands ---

    @rolebutton_group.command(
        name="create", description="Post a new message with a self-assignable role button."
    )
    @app_commands.describe(
        channel="Channel to post the message in.",
        role="The role this button grants/removes on click.",
        message="The message text to post.",
        emoji="Optional emoji to show on the button.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolebutton_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        message: str = "Click a button below to get a role!",
        emoji: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        normalized_emoji = None
        if emoji is not None:
            try:
                normalized_emoji = normalize_emoji(emoji)
            except InvalidEmojiError as exc:
                await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
                return

        custom_id = f"{BUTTON_CUSTOM_ID_PREFIX}{uuid4().hex}"
        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.primary, label=role.name, custom_id=custom_id, emoji=normalized_emoji
            )
        )
        try:
            posted = await channel.send(message, view=view)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't post that message ({exc}).", ephemeral=True)
            return

        try:
            await self.role_service.create_button_binding(
                interaction.guild.id,
                channel_id=channel.id,
                message_id=posted.id,
                role_id=role.id,
                custom_id=custom_id,
                emoji=normalized_emoji,
            )
        except RoleBindingValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Role button created in {channel.mention}: {role.mention}.", ephemeral=True
        )

    @rolebutton_group.command(
        name="add", description="Add a self-assignable role button to an existing message."
    )
    @app_commands.describe(
        message_link="Link to the message (right-click it, then 'Copy Message Link').",
        role="The role this button grants/removes on click.",
        emoji="Optional emoji to show on the button.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolebutton_add(
        self, interaction: discord.Interaction, message_link: str, role: discord.Role, emoji: str | None = None
    ) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        channel, message = resolved

        normalized_emoji = None
        if emoji is not None:
            try:
                normalized_emoji = normalize_emoji(emoji)
            except InvalidEmojiError as exc:
                await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
                return

        custom_id = f"{BUTTON_CUSTOM_ID_PREFIX}{uuid4().hex}"
        try:
            await self.role_service.create_button_binding(
                interaction.guild.id,
                channel_id=channel.id,
                message_id=message.id,
                role_id=role.id,
                custom_id=custom_id,
                emoji=normalized_emoji,
            )
        except RoleBindingValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        bindings = await self.role_service.list_for_message(
            interaction.guild.id, message.id, RoleBindingType.BUTTON
        )
        view = self._build_button_view(interaction.guild, bindings)
        try:
            await message.edit(view=view)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't update that message ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Added a button for {role.mention}.", ephemeral=True)

    @rolebutton_group.command(name="remove", description="Remove one role's button from a message.")
    @app_commands.describe(message_link="Link to the message.", role="The role whose button to remove.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolebutton_remove(
        self, interaction: discord.Interaction, message_link: str, role: discord.Role
    ) -> None:
        assert interaction.guild is not None
        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        _channel, message = resolved

        removed = await self.role_service.remove_binding_by_role(
            interaction.guild.id, message.id, role.id, RoleBindingType.BUTTON
        )
        if not removed:
            await interaction.response.send_message("⚠️ That role doesn't have a button on this message.", ephemeral=True)
            return

        bindings = await self.role_service.list_for_message(
            interaction.guild.id, message.id, RoleBindingType.BUTTON
        )
        view = self._build_button_view(interaction.guild, bindings) if bindings else None
        try:
            await message.edit(view=view)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't update that message ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Removed the {role.mention} button.", ephemeral=True)

    @rolebutton_group.command(name="delete", description="Remove every role button from a message.")
    @app_commands.describe(message_link="Link to the message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolebutton_delete(self, interaction: discord.Interaction, message_link: str) -> None:
        assert interaction.guild is not None
        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        _channel, message = resolved

        count = await self.role_service.delete_message_bindings_by_type(
            interaction.guild.id, message.id, RoleBindingType.BUTTON
        )
        try:
            await message.edit(view=None)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't update that message ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Removed {count} button(s) from that message.", ephemeral=True)

    @rolebutton_group.command(name="list", description="List all role buttons in this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolebutton_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        bindings = await self.role_service.list_bindings(interaction.guild.id, RoleBindingType.BUTTON)
        if not bindings:
            await interaction.response.send_message("No role buttons configured.", ephemeral=True)
            return

        lines = []
        for binding in bindings:
            status = "✅" if binding.enabled else "⏸️"
            link = (
                f"https://discord.com/channels/{interaction.guild.id}/"
                f"{binding.source_channel_id}/{binding.source_message_id}"
            )
            lines.append(f"{status} <@&{binding.role_id}> - [message]({link})")

        description = "\n".join(lines)
        if len(description) > _LIST_DESCRIPTION_LIMIT:
            description = description[:_LIST_DESCRIPTION_LIMIT] + "\n…and more"

        embed = discord.Embed(title="Role Buttons", description=description, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /roleselect commands ---

    @roleselect_group.command(
        name="create", description="Post a new message with a self-assignable role select menu."
    )
    @app_commands.describe(
        channel="Channel to post the message in.",
        role="The first role option in the menu.",
        message="The message text to post.",
        placeholder="Placeholder text shown on the menu.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roleselect_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        message: str = "Pick your roles below!",
        placeholder: str = "Select roles...",
    ) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        try:
            posted = await channel.send(message)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't post that message ({exc}).", ephemeral=True)
            return

        try:
            await self.role_service.create_select_binding(
                interaction.guild.id, channel_id=channel.id, message_id=posted.id, role_id=role.id
            )
        except RoleBindingValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        bindings = await self.role_service.list_for_message(
            interaction.guild.id, posted.id, RoleBindingType.SELECT
        )
        view = self._build_select_view(interaction.guild, posted.id, bindings, placeholder)
        try:
            await posted.edit(view=view)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Posted, but couldn't attach the menu ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Role menu created in {channel.mention} with {role.mention}.", ephemeral=True
        )

    @roleselect_group.command(name="add", description="Add a role option to an existing select menu.")
    @app_commands.describe(
        message_link="Link to the message (right-click it, then 'Copy Message Link').",
        role="The role option to add.",
        placeholder="Placeholder text shown on the menu.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roleselect_add(
        self,
        interaction: discord.Interaction,
        message_link: str,
        role: discord.Role,
        placeholder: str = "Select roles...",
    ) -> None:
        assert interaction.guild is not None
        try:
            require_bot_can_manage_role(interaction.guild, role)
        except PermissionCheckError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        _channel, message = resolved

        try:
            await self.role_service.create_select_binding(
                interaction.guild.id, channel_id=message.channel.id, message_id=message.id, role_id=role.id
            )
        except RoleBindingValidationError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        bindings = await self.role_service.list_for_message(
            interaction.guild.id, message.id, RoleBindingType.SELECT
        )
        view = self._build_select_view(interaction.guild, message.id, bindings, placeholder)
        try:
            await message.edit(view=view)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't update that message ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Added {role.mention} to that menu.", ephemeral=True)

    @roleselect_group.command(name="remove", description="Remove one role option from a select menu.")
    @app_commands.describe(message_link="Link to the message.", role="The role option to remove.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roleselect_remove(
        self, interaction: discord.Interaction, message_link: str, role: discord.Role
    ) -> None:
        assert interaction.guild is not None
        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        _channel, message = resolved

        removed = await self.role_service.remove_binding_by_role(
            interaction.guild.id, message.id, role.id, RoleBindingType.SELECT
        )
        if not removed:
            await interaction.response.send_message("⚠️ That role isn't an option in this menu.", ephemeral=True)
            return

        bindings = await self.role_service.list_for_message(
            interaction.guild.id, message.id, RoleBindingType.SELECT
        )
        view = (
            self._build_select_view(interaction.guild, message.id, bindings, "Select roles...")
            if bindings
            else None
        )
        try:
            await message.edit(view=view)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't update that message ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Removed {role.mention} from that menu.", ephemeral=True)

    @roleselect_group.command(name="delete", description="Remove the select menu from a message.")
    @app_commands.describe(message_link="Link to the message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roleselect_delete(self, interaction: discord.Interaction, message_link: str) -> None:
        assert interaction.guild is not None
        resolved = await self._resolve_existing_message(interaction, message_link)
        if resolved is None:
            return
        _channel, message = resolved

        count = await self.role_service.delete_message_bindings_by_type(
            interaction.guild.id, message.id, RoleBindingType.SELECT
        )
        try:
            await message.edit(view=None)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"⚠️ Couldn't update that message ({exc}).", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Removed {count} option(s) from that menu.", ephemeral=True)

    @roleselect_group.command(name="list", description="List all role select menus in this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roleselect_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        bindings = await self.role_service.list_bindings(interaction.guild.id, RoleBindingType.SELECT)
        if not bindings:
            await interaction.response.send_message("No role select menus configured.", ephemeral=True)
            return

        by_message: dict[int, list[RoleBindingView]] = {}
        for binding in bindings:
            by_message.setdefault(binding.source_message_id, []).append(binding)

        lines = []
        for message_id, group in by_message.items():
            channel_id = group[0].source_channel_id
            link = f"https://discord.com/channels/{interaction.guild.id}/{channel_id}/{message_id}"
            roles = ", ".join(f"<@&{b.role_id}>" for b in group)
            lines.append(f"[message]({link}): {roles}")

        description = "\n".join(lines)
        if len(description) > _LIST_DESCRIPTION_LIMIT:
            description = description[:_LIST_DESCRIPTION_LIMIT] + "\n…and more"

        embed = discord.Embed(title="Role Select Menus", description=description, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))
