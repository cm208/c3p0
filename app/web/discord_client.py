"""Discord OAuth2 client for the web dashboard.

Deliberately not under app/services/ - this is HTTP-to-Discord's-REST-API
plumbing specific to the web login flow, not reusable business logic. It
mirrors app/music/*.py's precedent as the project's other documented,
deliberate exception to "services never touch an external system
directly".

All functions take an `httpx.AsyncClient` explicitly rather than
constructing one internally, so tests can pass one wired to
`httpx.MockTransport` instead of hitting the real Discord API.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode

import httpx

DISCORD_API_BASE = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"

# Discord permission bit flags relevant to "can this user manage this
# guild's C3P0 settings". See Discord's Permissions documentation for the
# full bitfield; these are the only two this dashboard cares about.
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8


class DiscordAPIError(Exception):
    """Raised when Discord's API returns an unexpected/error response."""


def has_manage_access(permissions: int) -> bool:
    """Whether `permissions` grants enough access to manage C3P0 here."""
    return bool(permissions & (MANAGE_GUILD | ADMINISTRATOR))


def build_authorize_url(*, client_id: int, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
            "prompt": "none",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


# The same permission set docs/discord-setup.md's invite link requests.
BOT_INVITE_PERMISSIONS = 1099783302230


def build_invite_url(*, client_id: int, guild_id: int) -> str:
    """Add-the-bot link, preselecting guild_id (the picker's [INVITE])."""
    query = urlencode(
        {
            "client_id": client_id,
            "scope": "bot applications.commands",
            "permissions": BOT_INVITE_PERMISSIONS,
            "guild_id": guild_id,
            "disable_guild_select": "true",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class DiscordUser:
    id: int
    username: str
    avatar: str | None


@dataclass(frozen=True, slots=True)
class DiscordUserGuild:
    id: int
    name: str
    icon: str | None
    permissions: int
    # Only populated when fetched with with_counts=True.
    approximate_member_count: int | None = None
    approximate_presence_count: int | None = None


@dataclass(frozen=True, slots=True)
class DiscordGuildCounts:
    member_count: int
    presence_count: int


@dataclass(frozen=True, slots=True)
class DiscordRole:
    id: int
    name: str
    position: int
    managed: bool


@dataclass(frozen=True, slots=True)
class DiscordChannel:
    id: int
    name: str
    type: int


@dataclass(frozen=True, slots=True)
class DiscordMember:
    id: int
    username: str
    display_name: str  # guild nick, else global display name, else username


# Channel types Discord considers "a text channel a bot can send plain
# messages/embeds to" - GUILD_TEXT and GUILD_ANNOUNCEMENT. discord.py's own
# `discord.TextChannel` parameter type (used by /config log-channel etc.)
# accepts both, so the web dashboard's dropdowns match that exactly rather
# than introducing a narrower or wider set of choices.
TEXT_CHANNEL_TYPES = frozenset({0, 5})

# Server-management-only channel type ids (Discord's own numbering -
# https://discord.com/developers/docs/resources/channel).
CATEGORY_CHANNEL_TYPE = 4
VOICE_CHANNEL_TYPES = frozenset({2})


# --- Server-management-only shapes. Kept separate from DiscordRole/
# DiscordChannel above (rather than widening those) so every existing
# router test's hand-built mock Discord JSON keeps working unchanged - none
# of it includes permission/color/topic/etc. fields those two lightweight
# types never needed.


@dataclass(frozen=True, slots=True)
class DiscordRoleDetail:
    id: int
    name: str
    position: int
    managed: bool
    color: int
    hoist: bool
    mentionable: bool
    permissions: int


@dataclass(frozen=True, slots=True)
class DiscordPermissionOverwrite:
    role_id: int
    allow: int
    deny: int


@dataclass(frozen=True, slots=True)
class DiscordChannelDetail:
    id: int
    name: str
    type: int
    position: int
    parent_id: int | None
    topic: str | None
    nsfw: bool
    rate_limit_per_user: int
    bitrate: int | None
    user_limit: int | None
    overwrites: list[DiscordPermissionOverwrite]


async def _post_token(
    http: httpx.AsyncClient,
    *,
    client_id: int,
    client_secret: str,
    data: dict[str, str],
) -> OAuthTokens:
    response = await http.post(
        f"{DISCORD_API_BASE}/oauth2/token",
        data={"client_id": str(client_id), "client_secret": client_secret, **data},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord token endpoint returned {response.status_code}")

    payload = response.json()
    return OAuthTokens(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_in=payload["expires_in"],
    )


async def exchange_code(
    http: httpx.AsyncClient,
    *,
    client_id: int,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> OAuthTokens:
    return await _post_token(
        http,
        client_id=client_id,
        client_secret=client_secret,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


async def refresh_access_token(
    http: httpx.AsyncClient,
    *,
    client_id: int,
    client_secret: str,
    refresh_token: str,
) -> OAuthTokens:
    return await _post_token(
        http,
        client_id=client_id,
        client_secret=client_secret,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )


async def fetch_current_user(http: httpx.AsyncClient, access_token: str) -> DiscordUser:
    response = await http.get(
        f"{DISCORD_API_BASE}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord /users/@me returned {response.status_code}")

    payload = response.json()
    return DiscordUser(id=int(payload["id"]), username=payload["username"], avatar=payload.get("avatar"))


async def fetch_user_guilds(
    http: httpx.AsyncClient, access_token: str, *, with_counts: bool = False
) -> list[DiscordUserGuild]:
    response = await http.get(
        f"{DISCORD_API_BASE}/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"with_counts": "true"} if with_counts else None,
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord /users/@me/guilds returned {response.status_code}")

    return [
        DiscordUserGuild(
            id=int(g["id"]),
            name=g["name"],
            icon=g.get("icon"),
            # Discord returns this as a string since permission bitfields
            # can exceed 32 bits.
            permissions=int(g["permissions"]),
            approximate_member_count=g.get("approximate_member_count"),
            approximate_presence_count=g.get("approximate_presence_count"),
        )
        for g in response.json()
    ]


async def fetch_guild_counts(
    http: httpx.AsyncClient, bot_token: str, guild_id: int
) -> DiscordGuildCounts:
    """Approximate member/online counts (Discord's own figures, refreshed
    by Discord every few minutes - the web process has no gateway presence
    data of its own)."""
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}",
        headers=_bot_headers(bot_token),
        params={"with_counts": "true"},
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord /guilds/{guild_id} returned {response.status_code}")
    data = response.json()
    return DiscordGuildCounts(
        member_count=data.get("approximate_member_count") or 0,
        presence_count=data.get("approximate_presence_count") or 0,
    )


def _bot_headers(bot_token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {bot_token}"}


async def fetch_guild_roles(
    http: httpx.AsyncClient, bot_token: str, guild_id: int
) -> list[DiscordRole]:
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/roles", headers=_bot_headers(bot_token)
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord /guilds/{guild_id}/roles returned {response.status_code}")

    return [
        DiscordRole(
            id=int(r["id"]), name=r["name"], position=r["position"], managed=r["managed"]
        )
        for r in response.json()
    ]


async def fetch_guild_channels(
    http: httpx.AsyncClient, bot_token: str, guild_id: int
) -> list[DiscordChannel]:
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/channels", headers=_bot_headers(bot_token)
    )
    if response.status_code != 200:
        raise DiscordAPIError(
            f"Discord /guilds/{guild_id}/channels returned {response.status_code}"
        )

    return [
        DiscordChannel(id=int(c["id"]), name=c["name"], type=c["type"])
        for c in response.json()
    ]


async def fetch_bot_role_ids(
    http: httpx.AsyncClient, bot_token: str, guild_id: int, bot_user_id: int
) -> list[int]:
    """The bot's own role IDs in `guild_id` - used to compute its top role
    position for hierarchy checks (see bot_top_role_position)."""
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{bot_user_id}",
        headers=_bot_headers(bot_token),
    )
    if response.status_code != 200:
        raise DiscordAPIError(
            f"Discord /guilds/{guild_id}/members/{bot_user_id} returned {response.status_code}"
        )

    return [int(role_id) for role_id in response.json()["roles"]]


async def fetch_guild_member(
    http: httpx.AsyncClient, bot_token: str, guild_id: int, user_id: int
) -> DiscordMember | None:
    """A guild member's display name, resolved by id.

    Returns None if the user isn't (or is no longer) a member of this
    guild - Discord 404s a departed member rather than returning a stale
    record, and that's the common case here: this exists for the
    moderation infraction log, where a kick or ban is exactly the
    situation that removes someone from the guild. Callers must treat
    "not found" as an expected, non-error outcome (fall back to showing
    the raw id), not a DiscordAPIError.
    """
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{user_id}",
        headers=_bot_headers(bot_token),
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DiscordAPIError(
            f"Discord /guilds/{guild_id}/members/{user_id} returned {response.status_code}"
        )

    payload = response.json()
    user = payload["user"]
    display_name = payload.get("nick") or user.get("global_name") or user["username"]
    return DiscordMember(id=int(user["id"]), username=user["username"], display_name=display_name)


def bot_top_role_position(roles: list[DiscordRole], bot_role_ids: list[int]) -> int:
    """The position of the bot's highest role, or 0 if it has none beyond @everyone.

    Mirrors app/cogs/admin.py's `me.top_role <= role` check: a role is only
    assignable by the bot if its position is strictly less than this.
    """
    bot_role_id_set = set(bot_role_ids)
    positions = [r.position for r in roles if r.id in bot_role_id_set]
    return max(positions, default=0)


# --- Roles-page-only: posting/editing messages and reactions via the bot
# token. Everything below exists because the web process has no
# discord.py Client/Message - it has to do by hand what
# channel.send()/message.edit()/message.add_reaction() do internally, and
# stay wire-compatible with what app/cogs/roles.py's listeners expect from
# messages the *bot itself* posts the normal way.


async def send_channel_message(
    http: httpx.AsyncClient,
    bot_token: str,
    channel_id: int,
    *,
    content: str,
    components: list[dict] | None = None,
) -> int:
    """Post a message, optionally with components. Returns the new message id."""
    body: dict = {"content": content}
    if components:
        body["components"] = components
    response = await http.post(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers=_bot_headers(bot_token),
        json=body,
    )
    if response.status_code != 200:
        raise DiscordAPIError(
            f"Discord POST /channels/{channel_id}/messages returned {response.status_code}"
        )
    return int(response.json()["id"])


async def edit_message_components(
    http: httpx.AsyncClient, bot_token: str, channel_id: int, message_id: int, components: list[dict]
) -> None:
    """Replace a message's components (or clear them with an empty list).

    Only sends "components" in the body - omitting "content"/"embeds"/etc.
    leaves them untouched, matching discord.py's own Message.edit()
    MISSING-sentinel semantics for unset kwargs.
    """
    response = await http.patch(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}",
        headers=_bot_headers(bot_token),
        json={"components": components},
    )
    if response.status_code != 200:
        raise DiscordAPIError(
            f"Discord PATCH /channels/{channel_id}/messages/{message_id} returned "
            f"{response.status_code}"
        )


async def add_own_reaction(
    http: httpx.AsyncClient, bot_token: str, channel_id: int, message_id: int, reaction_path_emoji: str
) -> None:
    response = await http.put(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/"
        f"{quote(reaction_path_emoji, safe='')}/@me",
        headers=_bot_headers(bot_token),
    )
    if response.status_code != 204:
        raise DiscordAPIError(
            f"Discord PUT .../reactions returned {response.status_code} for {channel_id}/{message_id}"
        )


async def clear_reaction(
    http: httpx.AsyncClient, bot_token: str, channel_id: int, message_id: int, reaction_path_emoji: str
) -> None:
    """Remove a reaction for everyone (best-effort cleanup) - 404 (already
    gone) is treated the same as success, not an error."""
    response = await http.delete(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/"
        f"{quote(reaction_path_emoji, safe='')}",
        headers=_bot_headers(bot_token),
    )
    if response.status_code not in (204, 404):
        raise DiscordAPIError(
            f"Discord DELETE .../reactions returned {response.status_code} for {channel_id}/{message_id}"
        )


# --- Server-management: role/channel lifecycle + permission overwrites.
# Same shape as everything above - explicit httpx.AsyncClient, bot-token
# auth, exact-status-code checks, frozen dataclass returns.


def _overwrite_from_payload(raw: dict) -> DiscordPermissionOverwrite:
    return DiscordPermissionOverwrite(
        role_id=int(raw["id"]), allow=int(raw["allow"]), deny=int(raw["deny"])
    )


def _role_detail_from_payload(raw: dict) -> DiscordRoleDetail:
    return DiscordRoleDetail(
        id=int(raw["id"]),
        name=raw["name"],
        position=raw["position"],
        managed=raw["managed"],
        color=raw["color"],
        hoist=raw["hoist"],
        mentionable=raw["mentionable"],
        permissions=int(raw["permissions"]),
    )


def _channel_detail_from_payload(raw: dict) -> DiscordChannelDetail:
    return DiscordChannelDetail(
        id=int(raw["id"]),
        name=raw["name"],
        type=raw["type"],
        position=raw.get("position", 0),
        parent_id=int(raw["parent_id"]) if raw.get("parent_id") else None,
        topic=raw.get("topic"),
        nsfw=raw.get("nsfw", False),
        rate_limit_per_user=raw.get("rate_limit_per_user", 0),
        bitrate=raw.get("bitrate"),
        user_limit=raw.get("user_limit"),
        overwrites=[
            _overwrite_from_payload(o) for o in raw.get("permission_overwrites", []) if o.get("type") == 0
        ],
    )


async def fetch_guild_roles_detailed(
    http: httpx.AsyncClient, bot_token: str, guild_id: int
) -> list[DiscordRoleDetail]:
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/roles", headers=_bot_headers(bot_token)
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord /guilds/{guild_id}/roles returned {response.status_code}")
    return [_role_detail_from_payload(r) for r in response.json()]


async def fetch_guild_channels_detailed(
    http: httpx.AsyncClient, bot_token: str, guild_id: int
) -> list[DiscordChannelDetail]:
    response = await http.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/channels", headers=_bot_headers(bot_token)
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord /guilds/{guild_id}/channels returned {response.status_code}")
    return [_channel_detail_from_payload(c) for c in response.json()]


async def create_guild_role(
    http: httpx.AsyncClient,
    bot_token: str,
    guild_id: int,
    *,
    name: str,
    color: int = 0,
    hoist: bool = False,
    mentionable: bool = False,
    permissions: int = 0,
) -> DiscordRoleDetail:
    response = await http.post(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/roles",
        headers=_bot_headers(bot_token),
        json={
            "name": name,
            "color": color,
            "hoist": hoist,
            "mentionable": mentionable,
            "permissions": str(permissions),
        },
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord POST /guilds/{guild_id}/roles returned {response.status_code}")
    return _role_detail_from_payload(response.json())


async def edit_guild_role(
    http: httpx.AsyncClient,
    bot_token: str,
    guild_id: int,
    role_id: int,
    *,
    name: str,
    color: int,
    hoist: bool,
    mentionable: bool,
    permissions: int,
) -> DiscordRoleDetail:
    response = await http.patch(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/roles/{role_id}",
        headers=_bot_headers(bot_token),
        json={
            "name": name,
            "color": color,
            "hoist": hoist,
            "mentionable": mentionable,
            "permissions": str(permissions),
        },
    )
    if response.status_code != 200:
        raise DiscordAPIError(
            f"Discord PATCH /guilds/{guild_id}/roles/{role_id} returned {response.status_code}"
        )
    return _role_detail_from_payload(response.json())


async def delete_guild_role(http: httpx.AsyncClient, bot_token: str, guild_id: int, role_id: int) -> None:
    response = await http.delete(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/roles/{role_id}", headers=_bot_headers(bot_token)
    )
    if response.status_code != 204:
        raise DiscordAPIError(
            f"Discord DELETE /guilds/{guild_id}/roles/{role_id} returned {response.status_code}"
        )


async def bulk_edit_role_positions(
    http: httpx.AsyncClient, bot_token: str, guild_id: int, moves: list[tuple[int, int]]
) -> None:
    """Reposition many roles in one Discord call - the drag-and-drop role
    canvas's Apply always submits every editable role's new position at
    once (the full editable set, not a diff), the same "one bulk PATCH"
    shape bulk_edit_channel_positions already uses for channels. Discord's
    role reorder endpoint returns the guild's full updated role list on
    success (200), unlike the channel one (204) - the body is ignored
    either way, since the caller (reorder_roles) redirects back to a fresh
    GET of the roles list rather than trusting either response body."""
    if not moves:
        return
    body = [{"id": str(role_id), "position": position} for role_id, position in moves]
    response = await http.patch(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/roles", headers=_bot_headers(bot_token), json=body
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord PATCH /guilds/{guild_id}/roles returned {response.status_code}")


async def create_guild_channel(
    http: httpx.AsyncClient,
    bot_token: str,
    guild_id: int,
    *,
    name: str,
    type: int,
    parent_id: int | None = None,
    topic: str | None = None,
    nsfw: bool = False,
    rate_limit_per_user: int = 0,
    bitrate: int | None = None,
    user_limit: int | None = None,
    overwrites: list[DiscordPermissionOverwrite] | None = None,
) -> DiscordChannelDetail:
    """Overwrites are set inline in the create body (Discord supports this
    directly) rather than via a separate PUT per overwrite - one clean
    success/failure boundary per channel, which matters for template-apply's
    per-item result reporting."""
    body: dict = {"name": name, "type": type}
    if parent_id is not None:
        body["parent_id"] = str(parent_id)
    if topic is not None:
        body["topic"] = topic
    if type == 0:  # text channel - these fields are meaningless for voice/category
        body["nsfw"] = nsfw
        body["rate_limit_per_user"] = rate_limit_per_user
    if type == 2:  # voice channel
        if bitrate is not None:
            body["bitrate"] = bitrate
        if user_limit is not None:
            body["user_limit"] = user_limit
    if overwrites:
        body["permission_overwrites"] = [
            {"id": str(o.role_id), "type": 0, "allow": str(o.allow), "deny": str(o.deny)}
            for o in overwrites
        ]

    response = await http.post(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/channels", headers=_bot_headers(bot_token), json=body
    )
    if response.status_code != 201:
        raise DiscordAPIError(
            f"Discord POST /guilds/{guild_id}/channels returned {response.status_code}"
        )
    return _channel_detail_from_payload(response.json())


async def edit_guild_channel(
    http: httpx.AsyncClient,
    bot_token: str,
    channel_id: int,
    *,
    name: str,
    parent_id: int | None,
    topic: str | None,
    nsfw: bool,
    rate_limit_per_user: int,
    bitrate: int | None,
    user_limit: int | None,
) -> DiscordChannelDetail:
    """Edits basic fields only - permission overwrites are managed one role
    at a time via put_channel_permission_overwrite/
    delete_channel_permission_overwrite instead, since an edit is normally
    diffing one role's overwrite against the channel's current set, not
    replacing the whole set at once."""
    body: dict = {
        "name": name,
        "parent_id": str(parent_id) if parent_id is not None else None,
        "topic": topic,
        "nsfw": nsfw,
        "rate_limit_per_user": rate_limit_per_user,
    }
    if bitrate is not None:
        body["bitrate"] = bitrate
    if user_limit is not None:
        body["user_limit"] = user_limit

    response = await http.patch(
        f"{DISCORD_API_BASE}/channels/{channel_id}", headers=_bot_headers(bot_token), json=body
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord PATCH /channels/{channel_id} returned {response.status_code}")
    return _channel_detail_from_payload(response.json())


async def delete_guild_channel(http: httpx.AsyncClient, bot_token: str, channel_id: int) -> None:
    # Discord's channel-delete endpoint uniquely returns 200 with the
    # deleted channel object, not 204 - unlike role delete above.
    response = await http.delete(
        f"{DISCORD_API_BASE}/channels/{channel_id}", headers=_bot_headers(bot_token)
    )
    if response.status_code != 200:
        raise DiscordAPIError(f"Discord DELETE /channels/{channel_id} returned {response.status_code}")


async def bulk_edit_channel_positions(
    http: httpx.AsyncClient, bot_token: str, guild_id: int, moves: list[tuple[int, int, int | None]]
) -> None:
    """Reposition/reparent many channels in one Discord call.

    Unlike edit_guild_role_position (deliberately restricted to a
    single-item array - roles only ever need one moved at a time from this
    dashboard's other pages), the channel canvas moves many channels in one
    "Apply", and Discord's channel reorder endpoint is inherently bulk: it
    takes the *complete* set of channels being repositioned in one PATCH,
    not one call per channel. `moves` is (channel_id, position, parent_id)
    triples; `parent_id` of None means "no category" (Discord distinguishes
    "unset" from "0" here, so it's only included in a move's body when not
    None - passing null explicitly still successfully clears a channel's
    category, so this is safe either way).
    """
    if not moves:
        return
    body = [
        {"id": str(channel_id), "position": position, "parent_id": str(parent_id) if parent_id is not None else None}
        for channel_id, position, parent_id in moves
    ]
    response = await http.patch(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/channels", headers=_bot_headers(bot_token), json=body
    )
    if response.status_code != 204:
        raise DiscordAPIError(f"Discord PATCH /guilds/{guild_id}/channels returned {response.status_code}")


async def put_channel_permission_overwrite(
    http: httpx.AsyncClient, bot_token: str, channel_id: int, *, role_id: int, allow: int, deny: int
) -> None:
    response = await http.put(
        f"{DISCORD_API_BASE}/channels/{channel_id}/permissions/{role_id}",
        headers=_bot_headers(bot_token),
        json={"allow": str(allow), "deny": str(deny), "type": 0},
    )
    if response.status_code != 204:
        raise DiscordAPIError(
            f"Discord PUT /channels/{channel_id}/permissions/{role_id} returned {response.status_code}"
        )


async def delete_channel_permission_overwrite(
    http: httpx.AsyncClient, bot_token: str, channel_id: int, role_id: int
) -> None:
    response = await http.delete(
        f"{DISCORD_API_BASE}/channels/{channel_id}/permissions/{role_id}",
        headers=_bot_headers(bot_token),
    )
    if response.status_code != 204:
        raise DiscordAPIError(
            f"Discord DELETE /channels/{channel_id}/permissions/{role_id} returned {response.status_code}"
        )
