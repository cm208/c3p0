"""Tests for the bot-side internal music control-plane API.

FastAPI's own TestClient talks directly to create_internal_app(...) - no
real bot, guild, or Docker involved. A hand-written FakeBot/FakeGuild
stands in for the discord.py objects the app reaches into (bot.get_guild,
guild.get_member, guild.get_channel, guild.voice_channels), matching the
FakeVoiceClient/FakeVoiceChannel shapes already established in
test_music_cog.py. AudioProvider.resolve/create_audio_source are
monkeypatched at the class level, same as that file, to avoid real network
calls and real ffmpeg invocation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.music.audio_provider import AudioProvider, AudioResolutionError, Track
from app.music.internal_api import create_internal_app
from app.services.music_service import MusicService

GUILD_ID = 1
TOKEN = "test-internal-token"
AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


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


def _make_voice_channel(channel_id: int, name: str = "General", *, connectable: bool = True) -> MagicMock:
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.name = name
    channel.connect = AsyncMock(side_effect=lambda: FakeVoiceClient(channel))
    channel.permissions_for = MagicMock(return_value=SimpleNamespace(connect=connectable))
    return channel


def _make_discord_member(user_id: int, display_name: str) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = display_name
    return member


def _make_text_channel(channel_id: int, name: str = "music-log") -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.send = AsyncMock()
    return channel


class FakeGuild:
    def __init__(
        self,
        guild_id: int,
        *,
        voice_channels: list | None = None,
        text_channels: list | None = None,
        members: dict[int, object] | None = None,
    ) -> None:
        self.id = guild_id
        self.voice_channels = voice_channels or []
        self.me = SimpleNamespace()
        self._members = members or {}
        self._channels_by_id = {c.id: c for c in [*self.voice_channels, *(text_channels or [])]}

    def get_member(self, user_id: int) -> object | None:
        return self._members.get(user_id)

    def get_channel(self, channel_id: int) -> object | None:
        return self._channels_by_id.get(channel_id)


class FakeBot:
    def __init__(self, guilds: dict[int, FakeGuild]) -> None:
        self._guilds = guilds

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guilds.get(guild_id)


def _fake_track(title: str, requested_by: int = 42) -> Track:
    return Track(
        title=title, stream_url=f"http://{title}", webpage_url=f"http://{title}", duration_seconds=100, requested_by=requested_by
    )


def _make_client(guild: FakeGuild, service: MusicService) -> TestClient:
    bot = FakeBot({guild.id: guild})
    app = create_internal_app(bot, music_service=service, internal_api_token=TOKEN)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_audio_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}")


# --- Auth ---


async def test_missing_token_is_rejected(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.get(f"/guilds/{GUILD_ID}/music/state")

    assert response.status_code == 401


async def test_wrong_token_is_rejected(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.get(f"/guilds/{GUILD_ID}/music/state", headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


async def test_unknown_guild_404s(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.get(f"/guilds/{999}/music/state", headers=AUTH_HEADERS)

    assert response.status_code == 404


# --- State ---


async def test_state_with_no_player_is_a_safe_empty_shape(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.get(f"/guilds/{GUILD_ID}/music/state", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["current"] is None
    assert body["queue"] == []


async def test_state_reflects_a_live_player(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song", requested_by=42)))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)], members={42: _make_discord_member(42, "Alice")})
    service = MusicService()
    client = _make_client(guild, service)

    add = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )
    assert add.status_code == 200

    state = client.get(f"/guilds/{GUILD_ID}/music/state", headers=AUTH_HEADERS).json()

    assert state["connected"] is True
    assert state["playing"] is True
    assert state["current"]["title"] == "Song"
    assert state["current"]["requested_by_name"] == "Alice"


# --- requested_by_name resolution ---


async def test_requested_by_name_falls_back_when_member_not_in_cache(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song", requested_by=42)))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)], members={})  # 42 not in cache
    client = _make_client(guild, MusicService())

    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )
    state = client.get(f"/guilds/{GUILD_ID}/music/state", headers=AUTH_HEADERS).json()

    assert state["current"]["requested_by_name"] == "User 42"


# --- Enqueue / cold-start ---


async def test_enqueue_cold_starts_connects_and_plays(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["started"] is True
    assert body["state"]["connected"] is True
    assert body["state"]["playing"] is True


async def test_enqueue_without_channel_while_disconnected_is_400(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400


async def test_enqueue_with_unknown_channel_id_is_404(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 999},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404


async def test_enqueue_second_track_is_queued_not_started(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B")]))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())

    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )
    second = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "b", "requested_by": 1},  # already connected - no channel needed
        headers=AUTH_HEADERS,
    )

    assert second.status_code == 200
    body = second.json()
    assert body["started"] is False
    assert body["state"]["current"]["title"] == "A"
    assert [t["title"] for t in body["state"]["queue"]] == ["B"]


async def test_enqueue_disabled_music_is_403(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    service = MusicService()
    await service.set_enabled(GUILD_ID, False)
    client = _make_client(guild, service)

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 403


async def test_enqueue_resolution_error_is_422(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=AudioResolutionError("No results found.")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "nonsense", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "No results found."


async def test_enqueue_full_queue_is_409(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    # max_queue_size=1 caps the *waiting* queue, not `current` - the first
    # enqueue starts playing immediately (queue drains back to 0), so it
    # takes a second enqueue to actually fill the queue before a third one
    # can overflow it.
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B"), _fake_track("C")]))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    service = MusicService()
    await service.set_max_queue_size(GUILD_ID, 1)
    client = _make_client(guild, service)

    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )
    client.post(f"/guilds/{GUILD_ID}/music/enqueue", json={"query": "b", "requested_by": 1}, headers=AUTH_HEADERS)
    third = client.post(f"/guilds/{GUILD_ID}/music/enqueue", json={"query": "c", "requested_by": 1}, headers=AUTH_HEADERS)

    assert third.status_code == 409


# --- Transport controls ---


async def test_pause_then_resume(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    paused = client.post(f"/guilds/{GUILD_ID}/music/pause", headers=AUTH_HEADERS)
    assert paused.status_code == 200
    assert paused.json()["paused"] is True

    resumed = client.post(f"/guilds/{GUILD_ID}/music/resume", headers=AUTH_HEADERS)
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False


async def test_pause_with_nothing_playing_is_404(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.post(f"/guilds/{GUILD_ID}/music/pause", headers=AUTH_HEADERS)

    assert response.status_code == 404


async def test_resume_with_nothing_paused_is_404(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.post(f"/guilds/{GUILD_ID}/music/resume", headers=AUTH_HEADERS)

    assert response.status_code == 404


async def test_skip_with_nothing_playing_is_404(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.post(f"/guilds/{GUILD_ID}/music/skip", headers=AUTH_HEADERS)

    assert response.status_code == 404


async def test_skip_stops_current_track(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    # Real playback only advances when discord.py's `after` callback fires
    # once the stopped source actually finishes (deferred via
    # run_coroutine_threadsafe - see GuildPlayer.start_or_advance) - the
    # FakeVoiceClient here, like test_guild_player.py's, doesn't simulate
    # that automatically, so /skip itself is only responsible for stopping
    # playback (mirrors MusicCog.skip, which doesn't call start_or_advance
    # either). That deferred-advance behavior is already covered by
    # test_guild_player.py's own loop/skip tests.
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("A")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    service = MusicService()
    client = _make_client(guild, service)
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    response = client.post(f"/guilds/{GUILD_ID}/music/skip", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["playing"] is False
    assert service.get_player(GUILD_ID).voice_client.is_playing() is False


# --- Volume ---


async def test_set_volume_success(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.post(f"/guilds/{GUILD_ID}/music/volume", json={"percent": 42}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["volume_percent"] == 42


async def test_set_volume_out_of_range_is_422(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.post(f"/guilds/{GUILD_ID}/music/volume", json={"percent": 150}, headers=AUTH_HEADERS)

    assert response.status_code == 422


# --- Queue removal ---


async def test_remove_queue_item_success(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B")]))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )
    client.post(f"/guilds/{GUILD_ID}/music/enqueue", json={"query": "b", "requested_by": 1}, headers=AUTH_HEADERS)

    response = client.request(
        "DELETE", f"/guilds/{GUILD_ID}/music/queue/0", headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["queue"] == []


async def test_remove_queue_item_stale_index_is_404(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("A")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    client = _make_client(guild, MusicService())
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    response = client.request("DELETE", f"/guilds/{GUILD_ID}/music/queue/5", headers=AUTH_HEADERS)

    assert response.status_code == 404


async def test_remove_queue_item_with_no_player_is_404(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    response = client.request("DELETE", f"/guilds/{GUILD_ID}/music/queue/0", headers=AUTH_HEADERS)

    assert response.status_code == 404


# --- Voice channel listing ---


async def test_voice_channels_filtered_by_connect_permission(db_session: AsyncSession) -> None:
    allowed = _make_voice_channel(10, name="General", connectable=True)
    denied = _make_voice_channel(11, name="Staff Only", connectable=False)
    guild = FakeGuild(GUILD_ID, voice_channels=[allowed, denied])
    client = _make_client(guild, MusicService())

    response = client.get(f"/guilds/{GUILD_ID}/music/voice-channels", headers=AUTH_HEADERS)

    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert names == ["General"]


# --- Web-dashboard enqueue -> Discord channel announcement ---


async def test_enqueue_announces_to_configured_music_channel(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song", requested_by=42)))
    text_channel = _make_text_channel(20)
    guild = FakeGuild(
        GUILD_ID, voice_channels=[_make_voice_channel(10)], text_channels=[text_channel],
        members={42: _make_discord_member(42, "Alice")},
    )
    service = MusicService()
    await service.set_music_channel(GUILD_ID, 20)
    client = _make_client(guild, service)

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    text_channel.send.assert_called_once()
    message = text_channel.send.call_args.args[0]
    assert "Song" in message
    assert "Alice" in message
    assert "started playing" in message  # first track - starts immediately


async def test_enqueue_announcement_mentions_queue_position_when_not_started(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B", requested_by=42)]))
    text_channel = _make_text_channel(20)
    guild = FakeGuild(
        GUILD_ID, voice_channels=[_make_voice_channel(10)], text_channels=[text_channel],
        members={42: _make_discord_member(42, "Alice")},
    )
    service = MusicService()
    await service.set_music_channel(GUILD_ID, 20)
    client = _make_client(guild, service)
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue", json={"query": "b", "requested_by": 42}, headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    message = text_channel.send.call_args.args[0]
    assert "B" in message
    assert "Alice" in message
    assert "added to the queue" in message


async def test_enqueue_does_not_announce_when_channel_not_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    text_channel = _make_text_channel(20)
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)], text_channels=[text_channel])
    client = _make_client(guild, MusicService())  # music_channel_id never set

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    text_channel.send.assert_not_called()


async def test_enqueue_announcement_failure_does_not_fail_the_request(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    text_channel = _make_text_channel(20)
    text_channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "Missing Access"))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)], text_channels=[text_channel])
    service = MusicService()
    await service.set_music_channel(GUILD_ID, 20)
    client = _make_client(guild, service)

    response = client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "some song", "requested_by": 42, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200  # the track was still queued successfully


# --- Restart / stop (the dashboard's |◄ PREV and ■ STOP) ---


async def test_restart_stops_current_track_for_replay(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("A")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    service = MusicService()
    client = _make_client(guild, service)
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    response = client.post(f"/guilds/{GUILD_ID}/music/restart", headers=AUTH_HEADERS)

    assert response.status_code == 200
    player = service.get_player(GUILD_ID)
    assert player._restart_once is True
    assert player.current.title == "A"


async def test_restart_with_nothing_playing_is_404(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    assert client.post(f"/guilds/{GUILD_ID}/music/restart", headers=AUTH_HEADERS).status_code == 404


async def test_stop_clears_queue_and_playback(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B")]))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10)])
    service = MusicService()
    client = _make_client(guild, service)
    for query in ("a", "b"):
        client.post(
            f"/guilds/{GUILD_ID}/music/enqueue",
            json={"query": query, "requested_by": 1, "voice_channel_id": 10},
            headers=AUTH_HEADERS,
        )

    response = client.post(f"/guilds/{GUILD_ID}/music/stop", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["current"] is None
    assert body["queue"] == []


async def test_stop_with_nothing_playing_is_404(db_session: AsyncSession) -> None:
    client = _make_client(FakeGuild(GUILD_ID), MusicService())

    assert client.post(f"/guilds/{GUILD_ID}/music/stop", headers=AUTH_HEADERS).status_code == 404


async def test_state_includes_the_voice_channel_name(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("A")))
    guild = FakeGuild(GUILD_ID, voice_channels=[_make_voice_channel(10, "Jukebox")])
    client = _make_client(guild, MusicService())
    client.post(
        f"/guilds/{GUILD_ID}/music/enqueue",
        json={"query": "a", "requested_by": 1, "voice_channel_id": 10},
        headers=AUTH_HEADERS,
    )

    state = client.get(f"/guilds/{GUILD_ID}/music/state", headers=AUTH_HEADERS).json()

    assert state["voice_channel_name"] == "Jukebox"


# --- /status (the dashboard's status bar + UPTIME tile) ---


class FakeStatusBot(FakeBot):
    latency = 0.042
    shard_id = None
    shard_count = None
    started_monotonic = 0.0

    def __init__(self, guilds: dict[int, FakeGuild], *, ready: bool = True) -> None:
        super().__init__(guilds)
        self._ready = ready
        self.guilds = list(guilds.values())
        self.started_at = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)

    def is_ready(self) -> bool:
        return self._ready


async def test_status_reports_version_latency_and_uptime(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = FakeStatusBot({GUILD_ID: FakeGuild(GUILD_ID)})
    monkeypatch.setattr("app.music.internal_api.time.monotonic", lambda: 3600.0)
    client = TestClient(create_internal_app(bot, music_service=MusicService(), internal_api_token=TOKEN))

    body = client.get("/status", headers=AUTH_HEADERS).json()

    assert body["version"] == __version__
    assert body["ready"] is True
    assert body["latency_ms"] == 42
    assert body["shard_id"] == 0 and body["shard_count"] == 1
    assert body["guild_count"] == 1
    assert body["uptime_seconds"] == 3600
    assert body["started_at"].startswith("2026-09-10T08:00:00")


async def test_status_before_first_heartbeat_has_no_latency(db_session: AsyncSession) -> None:
    bot = FakeStatusBot({}, ready=False)
    bot.latency = float("inf")
    client = TestClient(create_internal_app(bot, music_service=MusicService(), internal_api_token=TOKEN))

    body = client.get("/status", headers=AUTH_HEADERS).json()

    assert body["ready"] is False
    assert body["latency_ms"] is None


async def test_status_requires_the_token(db_session: AsyncSession) -> None:
    client = TestClient(create_internal_app(FakeStatusBot({}), music_service=MusicService(), internal_api_token=TOKEN))

    assert client.get("/status").status_code == 401
