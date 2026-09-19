"""Shared permission and role-hierarchy validation helpers.

These are deliberately framework-thin: they take plain discord.py objects
(Member/Role/Guild) and return booleans or raise a PermissionCheckError, so
they're usable from both cogs and services without pulling in
discord.Interaction specifics.
"""

from __future__ import annotations

import discord


class PermissionCheckError(Exception):
    """Raised when a permission/hierarchy check fails.

    The message is safe to show directly to the Discord user.
    """


def bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    """Whether the bot's top role is above the given role.

    A bot can never assign/remove a role at or above its own highest role.
    """
    me = guild.me
    if me is None:
        return False
    return me.top_role > role


def bot_can_moderate(guild: discord.Guild, target: discord.Member) -> bool:
    """Whether the bot's role position is above the target member's."""
    me = guild.me
    if me is None:
        return False
    if target.id == guild.owner_id:
        return False
    return me.top_role > target.top_role


def moderator_can_moderate(moderator: discord.Member, target: discord.Member) -> bool:
    """Whether the acting moderator outranks the target member.

    The guild owner can always act; otherwise the moderator's top role must
    be strictly above the target's.
    """
    if moderator.guild.owner_id == moderator.id:
        return True
    if target.id == moderator.guild.owner_id:
        return False
    return moderator.top_role > target.top_role


def require_bot_can_moderate(guild: discord.Guild, target: discord.Member) -> None:
    if not bot_can_moderate(guild, target):
        raise PermissionCheckError(
            "I can't take action on that member - my role isn't positioned above theirs."
        )


def require_moderator_can_moderate(moderator: discord.Member, target: discord.Member) -> None:
    if not moderator_can_moderate(moderator, target):
        raise PermissionCheckError("You can't take action on a member equal to or above you.")


def require_bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> None:
    if not bot_can_manage_role(guild, role):
        raise PermissionCheckError(
            f"I can't manage the {role.name!r} role - it's positioned above my highest role."
        )


# Duplicated from app/web/discord_client.py's ADMINISTRATOR rather than
# imported - this module is shared by the bot process too and must not
# depend on app/web/*.
_ADMINISTRATOR_BIT = 0x8


def has_all_permission_bits(*, held: int, requested: int) -> bool:
    """Whether `held` grants every bit set in `requested`.

    Administrator implies every other bit, mirroring Discord's own
    client-side rule. Used by the server-management dashboard feature to
    stop an operator from having a role (or a channel overwrite) grant a
    permission they don't themselves hold in that guild.
    """
    if held & _ADMINISTRATOR_BIT:
        return True
    return (requested & ~held) == 0
