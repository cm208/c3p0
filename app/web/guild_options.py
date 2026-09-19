"""Shared live-Discord-data loading for every guild-scoped dashboard page.

`assignable_roles` is filtered to what the bot can actually grant (not
managed, below the bot's own top role) - correct for anything that ends
up as `member.add_roles(...)` on the Discord side: General's default
role, Welcome's role-on-join, Roles' "role to grant".

`all_roles` is unfiltered (besides excluding @everyone, which would be a
confusing duplicate of the "Not set" option). Correct for anything that's
a plain membership check rather than a grant: Music's DJ role
(app/cogs/music.py's _check_dj just checks `role in ctx.author.roles`,
never touches bot hierarchy) and Custom Commands' role-restriction.
Filtering these through `assignable_roles` would wrongly hide a
legitimate role that happens to sit above the bot's own role.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import Request

from app.services.bot_guild_service import BotGuildService
from app.web.discord_client import (
    TEXT_CHANNEL_TYPES,
    DiscordAPIError,
    DiscordChannel,
    DiscordRole,
    bot_top_role_position,
    fetch_bot_role_ids,
    fetch_guild_channels,
    fetch_guild_roles,
)
from app.web.sessions import LoadedSession


@dataclass(frozen=True, slots=True)
class GuildDiscordState:
    all_roles: list[DiscordRole]
    assignable_roles: list[DiscordRole]
    text_channels: list[DiscordChannel]


async def load_guild_discord_state(request: Request, guild_id: int) -> GuildDiscordState:
    """Live roles/channels for guild_id, degrading to empty lists if Discord's
    API can't be reached right now rather than raising - callers must never
    trust a submitted role/channel id without re-checking it against this,
    so "nothing selectable" is always a safe (if inconvenient) fallback.

    The three Discord calls are independent (no endpoint's response feeds
    another's request), so they run concurrently rather than as three
    sequential round-trips - every guild-scoped page in the dashboard calls
    this, so its latency is a direct floor on how fast any of them can load.
    """
    config = request.app.state.web_config
    http = request.app.state.http_client

    try:
        roles, channels, bot_role_ids = await asyncio.gather(
            fetch_guild_roles(http, config.discord_bot_token, guild_id),
            fetch_guild_channels(http, config.discord_bot_token, guild_id),
            # A bot's user ID equals its application ID for the overwhelming
            # majority of Discord bots (this one included).
            fetch_bot_role_ids(http, config.discord_bot_token, guild_id, config.discord_client_id),
        )
    except DiscordAPIError:
        return GuildDiscordState(all_roles=[], assignable_roles=[], text_channels=[])

    top_position = bot_top_role_position(roles, bot_role_ids)
    all_roles = [r for r in roles if r.id != guild_id]
    assignable_roles = [r for r in all_roles if not r.managed and r.position < top_position]
    text_channels = [c for c in channels if c.type in TEXT_CHANNEL_TYPES]
    return GuildDiscordState(
        all_roles=all_roles, assignable_roles=assignable_roles, text_channels=text_channels
    )


async def guild_page_context(guild_id: int, session: LoadedSession, active_nav: str) -> dict:
    """The common base context every guild-scoped template needs."""
    guild_name = await BotGuildService().get_name(guild_id)
    return {
        "guild_id": guild_id,
        "guild_name": guild_name,
        "session": session,
        "active_nav": active_nav,
        "error": None,
    }


def resolve_optional_id(raw: str, allowed_ids: set[int]) -> tuple[int | None, str | None]:
    """Parse an optional dropdown value against a server-verified allow-list.

    Every guild-scoped page with a role/channel dropdown needs this - the
    submitted id must never be trusted without re-checking it against a
    freshly-fetched allowed set, since it can silently go stale between
    page load and submit (role deleted, channel deleted, hierarchy change).
    Returns (resolved_id, None) on success/clear, or (None, error_message).
    """
    if not raw:
        return None, None
    try:
        value = int(raw)
    except ValueError:
        return None, "Invalid selection."
    if value not in allowed_ids:
        return None, "That selection isn't available anymore - it may have been deleted or moved."
    return value, None
