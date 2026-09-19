from __future__ import annotations

import httpx
import pytest

from app.web.bot_client import (
    BotAPIConflictError,
    BotAPIError,
    BotAPINotFoundError,
    BotAPIUnprocessableError,
    VoiceChannelView,
    enqueue,
    get_player_state,
    list_voice_channels,
    pause,
    remove_queue_item,
    resume,
    set_volume,
    skip,
)

TOKEN = "internal-secret"
BASE_URL = "http://c3p0:8100"
GUILD_ID = 1

_EMPTY_STATE = {
    "connected": False,
    "voice_channel_id": None,
    "playing": False,
    "paused": False,
    "current": None,
    "elapsed_seconds": None,
    "volume_percent": 0,
    "loop_mode": "off",
    "queue": [],
    "max_queue_size": 0,
}

_TRACK = {
    "title": "Song",
    "webpage_url": "http://song",
    "duration_seconds": 100,
    "requested_by": 42,
    "requested_by_name": "Alice",
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_get_player_state_sends_bearer_token_and_parses_response() -> None:
    seen_auth = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        assert request.url.path == f"/guilds/{GUILD_ID}/music/state"
        return httpx.Response(200, json={**_EMPTY_STATE, "connected": True, "volume_percent": 50})

    async with _client(handler) as http:
        state = await get_player_state(http, BASE_URL, TOKEN, GUILD_ID)

    assert seen_auth == [f"Bearer {TOKEN}"]
    assert state.connected is True
    assert state.volume_percent == 50


async def test_get_player_state_with_a_current_track() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_EMPTY_STATE, "connected": True, "current": _TRACK, "queue": [_TRACK]})

    async with _client(handler) as http:
        state = await get_player_state(http, BASE_URL, TOKEN, GUILD_ID)

    assert state.current is not None
    assert state.current.title == "Song"
    assert state.current.requested_by_name == "Alice"
    assert len(state.queue) == 1


async def test_list_voice_channels_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 10, "name": "General"}])

    async with _client(handler) as http:
        channels = await list_voice_channels(http, BASE_URL, TOKEN, GUILD_ID)

    assert channels == [VoiceChannelView(id=10, name="General")]


async def test_enqueue_returns_track_position_started_and_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = request.read()
        assert b'"query":"some song"' in body or b'"query": "some song"' in body
        return httpx.Response(
            200,
            json={
                "track": _TRACK,
                "position": 1,
                "started": True,
                "state": {**_EMPTY_STATE, "connected": True, "current": _TRACK},
            },
        )

    async with _client(handler) as http:
        track, position, started, state = await enqueue(
            http, BASE_URL, TOKEN, GUILD_ID, query="some song", requested_by=42, voice_channel_id=10
        )

    assert track.title == "Song"
    assert position == 1
    assert started is True
    assert state.connected is True


async def test_pause_resume_skip_set_volume_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_EMPTY_STATE)

    async with _client(handler) as http:
        assert (await pause(http, BASE_URL, TOKEN, GUILD_ID)).connected is False
        assert (await resume(http, BASE_URL, TOKEN, GUILD_ID)).connected is False
        assert (await skip(http, BASE_URL, TOKEN, GUILD_ID)).connected is False
        assert (await set_volume(http, BASE_URL, TOKEN, GUILD_ID, 50)).connected is False


async def test_remove_queue_item_sends_delete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == f"/guilds/{GUILD_ID}/music/queue/3"
        return httpx.Response(200, json=_EMPTY_STATE)

    async with _client(handler) as http:
        await remove_queue_item(http, BASE_URL, TOKEN, GUILD_ID, 3)


# --- Error mapping ---


async def test_404_response_raises_not_found_with_string_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Nothing is playing."})

    async with _client(handler) as http:
        with pytest.raises(BotAPINotFoundError, match="Nothing is playing."):
            await pause(http, BASE_URL, TOKEN, GUILD_ID)


async def test_409_response_raises_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Queue is full (max 5)."})

    async with _client(handler) as http:
        with pytest.raises(BotAPIConflictError, match="Queue is full"):
            await enqueue(http, BASE_URL, TOKEN, GUILD_ID, query="x", requested_by=1, voice_channel_id=10)


async def test_manually_raised_422_uses_the_plain_string_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "No results found."})

    async with _client(handler) as http:
        with pytest.raises(BotAPIUnprocessableError, match="No results found."):
            await enqueue(http, BASE_URL, TOKEN, GUILD_ID, query="x", requested_by=1, voice_channel_id=10)


async def test_pydantic_validation_422_does_not_leak_a_raw_list_repr() -> None:
    # A real Pydantic request-validation failure's `detail` is a list of
    # error objects, not a string - the client must not surface that
    # verbatim as a user-facing message.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": [{"loc": ["body", "percent"], "msg": "too large"}]})

    async with _client(handler) as http:
        with pytest.raises(BotAPIUnprocessableError) as exc_info:
            await set_volume(http, BASE_URL, TOKEN, GUILD_ID, 999)

    assert "loc" not in str(exc_info.value)
    assert "422" in str(exc_info.value)


async def test_unreachable_bot_raises_bot_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(handler) as http:
        with pytest.raises(BotAPIError, match="Couldn't reach"):
            await get_player_state(http, BASE_URL, TOKEN, GUILD_ID)
