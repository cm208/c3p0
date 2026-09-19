"""Router tests for both halves of the Music page: config (existing) and
live now-playing/queue/transport controls (new).

The live half is tested against the REAL app/music/internal_api.py FastAPI
app - not a hand-rolled fake responder - wired in via httpx.ASGITransport
so no real network/Docker is involved. A small FakeBot/FakeGuild stands in
for the discord.py objects that app reaches into, same shapes as
tests/test_internal_api.py (kept separately here, self-contained, matching
this codebase's existing norm of each test file owning its own small
fakes rather than sharing them - see test_guild_player.py vs.
test_music_cog.py's near-identical FakeVoiceClients).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.music.audio_provider import AudioProvider, Track
from app.music.internal_api import create_internal_app
from app.services.music_service import MusicService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111

# Same fixed test-guild shape as test_general_router.py, but the DJ role
# dropdown must include BOT_ROLE_ID too (managed, above the bot's own top
# role) - unlike assignable_roles, all_roles only excludes @everyone.
MEMBER_ROLE_ID = 500
BOT_ROLE_ID = 999
CHANNEL_A = 600
VOICE_CHANNEL_A = 700


def _discord_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/roles"):
        return httpx.Response(
            200,
            json=[
                {"id": str(GUILD_A), "name": "@everyone", "position": 0, "managed": False},
                {"id": str(MEMBER_ROLE_ID), "name": "Member", "position": 1, "managed": False},
                {"id": str(BOT_ROLE_ID), "name": "C3P0", "position": 2, "managed": True},
            ],
        )
    if path.endswith("/channels"):
        return httpx.Response(200, json=[{"id": str(CHANNEL_A), "name": "general", "type": 0}])
    if "/members/" in path:
        return httpx.Response(200, json={"roles": [str(BOT_ROLE_ID)]})
    raise AssertionError(f"unexpected Discord call: {request.url}")


# --- Fakes for the bot-side internal API (see app/music/internal_api.py) ---


class FakeVoiceClient:
    def __init__(self, channel: object) -> None:
        self.channel = channel
        self.source: object = None
        self._playing = False
        self._paused = False
        self.disconnected = False

    def is_connected(self) -> bool:
        return not self.disconnected

    def is_playing(self) -> bool:
        return self._playing and not self._paused

    def is_paused(self) -> bool:
        return self._paused

    def play(self, source: object, after: object = None) -> None:
        self.source = source
        self._playing = True
        self._paused = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._playing = False
        self._paused = False

    async def disconnect(self, force: bool = False) -> None:
        self.disconnected = True
        self._playing = False


def _make_voice_channel(channel_id: int, name: str = "General") -> MagicMock:
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.name = name
    channel.connect = AsyncMock(side_effect=lambda: FakeVoiceClient(channel))
    channel.permissions_for = MagicMock(return_value=SimpleNamespace(connect=True))
    return channel


class FakeGuild:
    def __init__(self, guild_id: int, *, voice_channels: list | None = None) -> None:
        self.id = guild_id
        self.voice_channels = voice_channels or []
        self.me = SimpleNamespace()
        self._channels_by_id = {c.id: c for c in self.voice_channels}

    def get_member(self, user_id: int) -> object | None:
        return None  # requested_by_name resolution is covered by test_internal_api.py

    def get_channel(self, channel_id: int) -> object | None:
        return self._channels_by_id.get(channel_id)


class FakeBot:
    def __init__(self, guilds: dict[int, FakeGuild]) -> None:
        self._guilds = guilds

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guilds.get(guild_id)


def _bot_http_client(guild: FakeGuild, service: MusicService, token: str) -> httpx.AsyncClient:
    """Talks to the REAL internal_api FastAPI app over an in-memory ASGI
    transport - no real network/Docker involved, but no hand-rolled fake
    responder duplicating that module's logic either."""
    app = create_internal_app(FakeBot({guild.id: guild}), music_service=service, internal_api_token=token)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), timeout=30.0)


def _unreachable_bot_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _fake_track(title: str, requested_by: int = 1) -> Track:
    return Track(
        title=title, stream_url=f"http://{title}", webpage_url=f"http://{title}", duration_seconds=100, requested_by=requested_by
    )


# --- Config (existing behavior, now exercised against a real - but empty -
# internal API instead of an unmocked live httpx client) ---


async def test_get_music_renders_current_config(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await MusicService().set_default_volume(GUILD_A, 75)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/music")

    assert response.status_code == 200
    assert 'value="75"' in response.text


async def test_get_music_dj_role_dropdown_includes_managed_and_above_hierarchy_roles(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/music")

    assert response.status_code == 200
    assert ">Member<" in response.text
    assert ">C3P0<" in response.text
    assert f'value="{GUILD_A}"' not in response.text


async def test_get_music_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/music")

    assert response.status_code == 403


async def test_post_music_updates_all_fields(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music",
                data={
                    "enabled": "on",
                    "default_volume": "60",
                    "max_queue_size": "25",
                    "dj_role_id": str(BOT_ROLE_ID),
                    "music_channel_id": str(CHANNEL_A),
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == f"/guilds/{GUILD_A}/music"

    config = await MusicService().get_config(GUILD_A)
    assert config.enabled is True
    assert config.default_volume == 60
    assert config.max_queue_size == 25
    assert config.dj_role_id == BOT_ROLE_ID
    assert config.music_channel_id == CHANNEL_A


async def test_post_music_rejects_out_of_range_volume(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music",
                data={"default_volume": "999", "max_queue_size": "100", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "between 0 and 100" in response.text
    config = await MusicService().get_config(GUILD_A)
    assert config.default_volume == 50


async def test_post_music_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music",
                data={"default_volume": "1", "max_queue_size": "1", "csrf_token": "wrong-token"},
                follow_redirects=False,
            )

    assert response.status_code == 403


async def test_post_music_404s_when_bot_absent(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music",
                data={"default_volume": "50", "max_queue_size": "100", "csrf_token": csrf_token},
            )

    assert response.status_code == 404


# --- Live state ---


async def test_get_music_renders_live_now_playing_and_queue(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    monkeypatch.setattr(AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}")

    guild = FakeGuild(GUILD_A, voice_channels=[_make_voice_channel(VOICE_CHANNEL_A)])
    service = MusicService()

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(guild, service, web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            player = await service.get_or_create_player(GUILD_A)
            await player.connect(guild.voice_channels[0])
            await player.resolve_and_enqueue("song", requested_by=1)
            await player.start_or_advance()

            response = client.get(f"/guilds/{GUILD_A}/music")

    assert response.status_code == 200
    assert "Song" in response.text


async def test_get_music_state_json_endpoint(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/music/state")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["queue"] == []


async def test_get_music_degrades_gracefully_when_bot_unreachable(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        httpx.AsyncClient(transport=httpx.MockTransport(_unreachable_bot_handler)) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/music")

    assert response.status_code == 200


# --- Add to queue ---


async def test_add_to_queue_cold_start_connects_and_plays(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    monkeypatch.setattr(AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}")

    guild = FakeGuild(GUILD_A, voice_channels=[_make_voice_channel(VOICE_CHANNEL_A)])
    service = MusicService()

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(guild, service, web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music/queue/add",
                data={"query": "some song", "voice_channel_id": str(VOICE_CHANNEL_A), "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    player = service.get_player(GUILD_A)
    assert player is not None
    assert player.current is not None
    assert player.current.title == "Song"


async def test_add_to_queue_without_channel_while_disconnected_rerenders_error(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    guild = FakeGuild(GUILD_A, voice_channels=[_make_voice_channel(VOICE_CHANNEL_A)])

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(guild, MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music/queue/add",
                data={"query": "some song", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "voice channel" in response.text


async def test_add_to_queue_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music/queue/add",
                data={"query": "some song", "csrf_token": "wrong-token"},
            )

    assert response.status_code == 403


# --- Remove from queue ---


async def test_remove_from_queue_success(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B")]))
    monkeypatch.setattr(AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}")

    guild = FakeGuild(GUILD_A, voice_channels=[_make_voice_channel(VOICE_CHANNEL_A)])
    service = MusicService()

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(guild, service, web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            player = await service.get_or_create_player(GUILD_A)
            await player.connect(guild.voice_channels[0])
            await player.resolve_and_enqueue("a", requested_by=1)
            await player.start_or_advance()
            await player.resolve_and_enqueue("b", requested_by=1)

            response = client.post(
                f"/guilds/{GUILD_A}/music/queue/0/remove",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert player.queue == []


async def test_remove_from_queue_stale_index_is_404(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music/queue/0/remove", data={"csrf_token": csrf_token}
            )

    assert response.status_code == 404


# --- Transport controls ---


async def test_pause_resume_skip_round_trip(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    monkeypatch.setattr(AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}")

    guild = FakeGuild(GUILD_A, voice_channels=[_make_voice_channel(VOICE_CHANNEL_A)])
    service = MusicService()

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(guild, service, web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            player = await service.get_or_create_player(GUILD_A)
            await player.connect(guild.voice_channels[0])
            await player.resolve_and_enqueue("song", requested_by=1)
            await player.start_or_advance()

            pause_response = client.post(
                f"/guilds/{GUILD_A}/music/player/pause", data={"csrf_token": csrf_token}, follow_redirects=False
            )
            assert pause_response.status_code == 303
            assert player.voice_client.is_paused() is True

            resume_response = client.post(
                f"/guilds/{GUILD_A}/music/player/resume", data={"csrf_token": csrf_token}, follow_redirects=False
            )
            assert resume_response.status_code == 303
            assert player.voice_client.is_paused() is False

            skip_response = client.post(
                f"/guilds/{GUILD_A}/music/player/skip", data={"csrf_token": csrf_token}, follow_redirects=False
            )
            assert skip_response.status_code == 303
            assert player.voice_client.is_playing() is False


async def test_pause_with_nothing_playing_rerenders_with_error(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(f"/guilds/{GUILD_A}/music/player/pause", data={"csrf_token": csrf_token})

    assert response.status_code == 400
    assert "Nothing is playing" in response.text


async def test_pause_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(f"/guilds/{GUILD_A}/music/player/pause", data={"csrf_token": csrf_token})

    assert response.status_code == 403


# --- Volume ---


async def test_set_volume_success(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    service = MusicService()

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), service, web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music/player/volume",
                data={"percent": "42", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert service.get_player(GUILD_A).volume == pytest.approx(0.42)


async def test_set_volume_out_of_range_rerenders_with_error(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http,
        _bot_http_client(FakeGuild(GUILD_A), MusicService(), web_config.internal_api_token) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/music/player/volume", data={"percent": "999", "csrf_token": csrf_token}
            )

    assert response.status_code == 400
    assert "between 0 and 100" in response.text
