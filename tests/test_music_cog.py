"""Tests for MusicCog's prefix commands.

Cog prefix commands are invoked directly via `cog.command.callback(cog,
ctx, ...)`, mirroring tests/test_moderation_cog.py's approach (see its
module docstring for why this is the correct calling convention).
AudioProvider.resolve/create_audio_source are monkeypatched at the class
level to avoid real network calls and real ffmpeg invocation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cogs.music import MusicCog
from app.music.audio_provider import AudioProvider, Track

GUILD_ID = 1


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

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._playing = False
        self._paused = False

    async def move_to(self, channel: object) -> None:
        self.channel = channel

    async def disconnect(self, force: bool = False) -> None:
        self.disconnected = True
        self._playing = False


class FakeVoiceChannel:
    def __init__(self, channel_id: int = 10) -> None:
        self.id = channel_id
        self.mention = f"<#{channel_id}>"

    async def connect(self) -> FakeVoiceClient:
        return FakeVoiceClient(self)


def _make_member(
    member_id: int, *, voice_channel: object | None = None, role_ids: set[int] | None = None, manage_guild: bool = False
) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.mention = f"<@{member_id}>"
    member.voice = SimpleNamespace(channel=voice_channel) if voice_channel is not None else None
    member.roles = [SimpleNamespace(id=rid) for rid in (role_ids or set())]
    member.guild_permissions = SimpleNamespace(manage_guild=manage_guild)
    return member


class _NullAsyncContext:
    async def __aenter__(self) -> _NullAsyncContext:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeContext:
    def __init__(self, guild_id: int, author: MagicMock, *, members: dict[int, object] | None = None) -> None:
        self.guild = SimpleNamespace(id=guild_id, get_member=(members or {}).get)
        self.author = author
        self.sent: list[object] = []

    def typing(self) -> _NullAsyncContext:
        return _NullAsyncContext()

    async def send(self, content: object | None = None, *, embed: object | None = None, **_: object) -> None:
        self.sent.append(content if content is not None else embed)


def _make_cog() -> MusicCog:
    bot = SimpleNamespace(default_prefix="!")
    return MusicCog(bot)  # type: ignore[arg-type]


def _fake_track(title: str, requested_by: int = 1) -> Track:
    return Track(
        title=title, stream_url=f"http://{title}", webpage_url=f"http://{title}", duration_seconds=100, requested_by=requested_by
    )


async def test_join_connects_to_authors_channel(db_session: AsyncSession) -> None:
    cog = _make_cog()
    channel = FakeVoiceChannel()
    author = _make_member(1, voice_channel=channel)
    ctx = FakeContext(GUILD_ID, author)

    await cog.join.callback(cog, ctx)  # type: ignore[arg-type]

    player = cog.service.get_player(GUILD_ID)
    assert player is not None
    assert player.voice_client is not None
    assert player.voice_client.channel is channel
    assert any("Joined" in str(m) for m in ctx.sent)


async def test_join_requires_voice_channel(db_session: AsyncSession) -> None:
    cog = _make_cog()
    author = _make_member(1)
    ctx = FakeContext(GUILD_ID, author)

    await cog.join.callback(cog, ctx)  # type: ignore[arg-type]

    assert cog.service.get_player(GUILD_ID) is None
    assert any("voice channel" in str(m) for m in ctx.sent)


async def test_play_enqueues_and_starts_playback(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = _make_cog()
    channel = FakeVoiceChannel()
    author = _make_member(1, voice_channel=channel)
    ctx = FakeContext(GUILD_ID, author)

    monkeypatch.setattr(AudioProvider, "resolve", AsyncMock(return_value=_fake_track("Song")))
    monkeypatch.setattr(
        AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}"
    )

    await cog.play.callback(cog, ctx, query="some song")  # type: ignore[arg-type]

    player = cog.service.get_player(GUILD_ID)
    assert player is not None
    assert player.current is not None
    assert player.current.title == "Song"
    assert any("Now playing" in str(m) for m in ctx.sent)
    assert any(author.mention in str(m) for m in ctx.sent)


async def test_play_requires_voice_channel(db_session: AsyncSession) -> None:
    cog = _make_cog()
    author = _make_member(1)
    ctx = FakeContext(GUILD_ID, author)

    await cog.play.callback(cog, ctx, query="song")  # type: ignore[arg-type]

    assert any("voice channel" in str(m) for m in ctx.sent)


async def test_play_second_track_is_queued_not_started(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = _make_cog()
    channel = FakeVoiceChannel()
    author = _make_member(1, voice_channel=channel)
    ctx = FakeContext(GUILD_ID, author)

    monkeypatch.setattr(
        AudioProvider, "resolve", AsyncMock(side_effect=[_fake_track("A"), _fake_track("B")])
    )
    monkeypatch.setattr(
        AudioProvider, "create_audio_source", lambda self, track, volume: f"source:{track.title}"
    )

    await cog.play.callback(cog, ctx, query="a")  # type: ignore[arg-type]
    await cog.play.callback(cog, ctx, query="b")  # type: ignore[arg-type]

    player = cog.service.get_player(GUILD_ID)
    assert player is not None
    assert player.current.title == "A"
    assert [t.title for t in player.queue] == ["B"]
    assert any("Queued" in str(m) and author.mention in str(m) for m in ctx.sent)


async def test_pause_blocked_without_dj_role(db_session: AsyncSession) -> None:
    cog = _make_cog()
    await cog.service.set_dj_role(GUILD_ID, 999)
    channel = FakeVoiceChannel()
    author = _make_member(1, voice_channel=channel)  # no roles, not manage_guild
    ctx = FakeContext(GUILD_ID, author)
    player = await cog.service.get_or_create_player(GUILD_ID)
    await player.connect(channel)
    player.voice_client._playing = True  # type: ignore[attr-defined]

    await cog.pause.callback(cog, ctx)  # type: ignore[arg-type]

    assert player.voice_client.is_paused() is False  # type: ignore[union-attr]
    assert any("DJ role" in str(m) for m in ctx.sent)


async def test_pause_allowed_with_dj_role(db_session: AsyncSession) -> None:
    cog = _make_cog()
    await cog.service.set_dj_role(GUILD_ID, 999)
    channel = FakeVoiceChannel()
    author = _make_member(1, voice_channel=channel, role_ids={999})
    ctx = FakeContext(GUILD_ID, author)
    player = await cog.service.get_or_create_player(GUILD_ID)
    await player.connect(channel)
    player.voice_client._playing = True  # type: ignore[attr-defined]

    await cog.pause.callback(cog, ctx)  # type: ignore[arg-type]

    assert player.voice_client.is_paused() is True  # type: ignore[union-attr]


async def test_queue_command_shows_empty_message(db_session: AsyncSession) -> None:
    cog = _make_cog()
    author = _make_member(1)
    ctx = FakeContext(GUILD_ID, author)

    await cog.queue.callback(cog, ctx)  # type: ignore[arg-type]

    assert ctx.sent == ["The queue is empty."]


async def test_queue_command_shows_requester_names(db_session: AsyncSession) -> None:
    cog = _make_cog()
    channel = FakeVoiceChannel()
    author = _make_member(1, voice_channel=channel)
    cached_member = _make_member(2)
    cached_member.display_name = "Alice"
    ctx = FakeContext(GUILD_ID, author, members={2: cached_member})

    player = await cog.service.get_or_create_player(GUILD_ID)
    player._provider.create_audio_source = lambda track, volume: f"source:{track.title}"  # type: ignore[method-assign]
    await player.connect(channel)
    player.enqueue(_fake_track("Now Playing Song", requested_by=2))
    await player.start_or_advance()
    player.enqueue(_fake_track("Upcoming Song", requested_by=99))  # not in the member cache

    await cog.queue.callback(cog, ctx)  # type: ignore[arg-type]

    embed = ctx.sent[0]
    assert "requested by Alice" in embed.description
    assert "requested by user 99" in embed.description


async def test_volume_rejects_out_of_range(db_session: AsyncSession) -> None:
    cog = _make_cog()
    author = _make_member(1)
    ctx = FakeContext(GUILD_ID, author)

    await cog.volume.callback(cog, ctx, 150)  # type: ignore[arg-type]

    assert any("between 0 and 100" in str(m) for m in ctx.sent)


async def test_loop_rejects_invalid_mode(db_session: AsyncSession) -> None:
    cog = _make_cog()
    author = _make_member(1)
    ctx = FakeContext(GUILD_ID, author)

    await cog.loop.callback(cog, ctx, "sideways")  # type: ignore[arg-type]

    assert any("off" in str(m) and "song" in str(m) for m in ctx.sent)
