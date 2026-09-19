"""Client for the bot's internal music control-plane API.

Mirrors discord_client.py's shape (explicit httpx.AsyncClient param so
tests can pass one wired to httpx.MockTransport, frozen dataclass views, a
dedicated exception per failure mode) - but talks to app/music/
internal_api.py (inside the bot's own container, over the Compose network)
rather than Discord itself. See that module's docstring for why this API
exists and what authenticates it.

Unlike discord_client.py, connection-level failures (the bot container
mid-restart, DNS not yet resolving the Compose service name) are caught
and folded into BotAPIError too, alongside real error responses - the
router only ever needs to catch one exception type to degrade gracefully,
and a briefly-unreachable bot container is a routine, expected condition
for this API (every deploy passes through it), not an exotic edge case.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class BotAPIError(Exception):
    """Raised when the bot's internal API can't be reached or returns an
    unexpected/error response."""


class BotAPINotFoundError(BotAPIError):
    """404 - no guild/player/queue-item/voice-channel matching the request."""


class BotAPIConflictError(BotAPIError):
    """409 - the requested change can't be applied right now (e.g. queue full)."""


class BotAPIUnprocessableError(BotAPIError):
    """400/422 - the request itself is invalid (bad query, bad volume, disabled)."""


@dataclass(frozen=True, slots=True)
class TrackView:
    title: str
    webpage_url: str
    duration_seconds: int | None
    requested_by: int
    requested_by_name: str


@dataclass(frozen=True, slots=True)
class PlayerStateView:
    connected: bool
    voice_channel_id: int | None
    playing: bool
    paused: bool
    current: TrackView | None
    elapsed_seconds: float | None
    volume_percent: int
    loop_mode: str
    queue: list[TrackView]
    max_queue_size: int


@dataclass(frozen=True, slots=True)
class VoiceChannelView:
    id: int
    name: str


# A safe "can't reach the bot" fallback - lets the dashboard page still
# render (degraded) instead of 500ing just because c3p0 is mid-restart.
UNREACHABLE_STATE = PlayerStateView(
    connected=False,
    voice_channel_id=None,
    playing=False,
    paused=False,
    current=None,
    elapsed_seconds=None,
    volume_percent=0,
    loop_mode="off",
    queue=[],
    max_queue_size=0,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _call(http: httpx.AsyncClient, method: str, url: str, *, description: str, **kwargs: object) -> dict | list:
    try:
        response = await http.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise BotAPIError(f"Couldn't reach the bot's internal API ({description}): {exc}") from exc

    if response.status_code >= 400:
        # FastAPI's 422 body is a Pydantic validation *list* for a malformed
        # request shape, but a plain *string* for a manually raised
        # HTTPException(422, detail=str(exc)) (e.g. AudioResolutionError) -
        # only the string form is safe to surface directly to a user.
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        message = detail if isinstance(detail, str) and detail else f"{description} returned {response.status_code}"
        if response.status_code == 404:
            raise BotAPINotFoundError(message)
        if response.status_code == 409:
            raise BotAPIConflictError(message)
        if response.status_code in (400, 422):
            raise BotAPIUnprocessableError(message)
        raise BotAPIError(message)

    return response.json()


def _track_view(data: dict) -> TrackView:
    return TrackView(
        title=data["title"],
        webpage_url=data["webpage_url"],
        duration_seconds=data["duration_seconds"],
        requested_by=data["requested_by"],
        requested_by_name=data["requested_by_name"],
    )


def _state_view(data: dict) -> PlayerStateView:
    return PlayerStateView(
        connected=data["connected"],
        voice_channel_id=data["voice_channel_id"],
        playing=data["playing"],
        paused=data["paused"],
        current=_track_view(data["current"]) if data["current"] is not None else None,
        elapsed_seconds=data["elapsed_seconds"],
        volume_percent=data["volume_percent"],
        loop_mode=data["loop_mode"],
        queue=[_track_view(t) for t in data["queue"]],
        max_queue_size=data["max_queue_size"],
    )


async def get_player_state(http: httpx.AsyncClient, base_url: str, token: str, guild_id: int) -> PlayerStateView:
    data = await _call(
        http, "GET", f"{base_url}/guilds/{guild_id}/music/state",
        headers=_headers(token), description=f"GET /guilds/{guild_id}/music/state",
    )
    return _state_view(data)


async def list_voice_channels(
    http: httpx.AsyncClient, base_url: str, token: str, guild_id: int
) -> list[VoiceChannelView]:
    data = await _call(
        http, "GET", f"{base_url}/guilds/{guild_id}/music/voice-channels",
        headers=_headers(token), description=f"GET /guilds/{guild_id}/music/voice-channels",
    )
    return [VoiceChannelView(id=c["id"], name=c["name"]) for c in data]


async def enqueue(
    http: httpx.AsyncClient,
    base_url: str,
    token: str,
    guild_id: int,
    *,
    query: str,
    requested_by: int,
    voice_channel_id: int | None,
) -> tuple[TrackView, int, bool, PlayerStateView]:
    """Returns (track, queue_position, started, fresh_state)."""
    data = await _call(
        http, "POST", f"{base_url}/guilds/{guild_id}/music/enqueue",
        headers=_headers(token),
        json={"query": query, "requested_by": requested_by, "voice_channel_id": voice_channel_id},
        timeout=30.0,  # yt-dlp resolution routinely takes several seconds
        description=f"POST /guilds/{guild_id}/music/enqueue",
    )
    return _track_view(data["track"]), data["position"], data["started"], _state_view(data["state"])


async def remove_queue_item(
    http: httpx.AsyncClient, base_url: str, token: str, guild_id: int, index: int
) -> PlayerStateView:
    data = await _call(
        http, "DELETE", f"{base_url}/guilds/{guild_id}/music/queue/{index}",
        headers=_headers(token), description=f"DELETE /guilds/{guild_id}/music/queue/{index}",
    )
    return _state_view(data)


async def pause(http: httpx.AsyncClient, base_url: str, token: str, guild_id: int) -> PlayerStateView:
    data = await _call(
        http, "POST", f"{base_url}/guilds/{guild_id}/music/pause",
        headers=_headers(token), description=f"POST /guilds/{guild_id}/music/pause",
    )
    return _state_view(data)


async def resume(http: httpx.AsyncClient, base_url: str, token: str, guild_id: int) -> PlayerStateView:
    data = await _call(
        http, "POST", f"{base_url}/guilds/{guild_id}/music/resume",
        headers=_headers(token), description=f"POST /guilds/{guild_id}/music/resume",
    )
    return _state_view(data)


async def skip(http: httpx.AsyncClient, base_url: str, token: str, guild_id: int) -> PlayerStateView:
    data = await _call(
        http, "POST", f"{base_url}/guilds/{guild_id}/music/skip",
        headers=_headers(token), description=f"POST /guilds/{guild_id}/music/skip",
    )
    return _state_view(data)


async def set_volume(
    http: httpx.AsyncClient, base_url: str, token: str, guild_id: int, percent: int
) -> PlayerStateView:
    data = await _call(
        http, "POST", f"{base_url}/guilds/{guild_id}/music/volume",
        headers=_headers(token), json={"percent": percent}, description=f"POST /guilds/{guild_id}/music/volume",
    )
    return _state_view(data)
