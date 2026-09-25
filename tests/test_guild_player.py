"""GuildPlayer tests.

AudioProvider.create_audio_source is stubbed on each player instance to
avoid spawning a real ffmpeg process (not installed in this dev
environment) - GuildPlayer's own logic (queue, loop modes, the skip-flag
fix) doesn't care what the "source" object actually is, only that
voice_client.play() gets called with something.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from app.music.audio_provider import Track
from app.music.guild_player import GuildPlayer, LoopMode, QueueFullError

GUILD_ID = 1


def _track(title: str = "Song") -> Track:
    return Track(
        title=title, stream_url="http://stream", webpage_url="http://page", duration_seconds=100, requested_by=1
    )


class FakeVoiceClient:
    def __init__(self, channel: object) -> None:
        self.channel = channel
        self.source: object = None
        self.after_callback = None
        self._playing = False
        self._paused = False
        self.stop_calls = 0
        self.disconnected = False
        self.moved_to: list[object] = []

    def is_connected(self) -> bool:
        return not self.disconnected

    def is_playing(self) -> bool:
        return self._playing and not self._paused

    def is_paused(self) -> bool:
        return self._paused

    def play(self, source: object, after: object = None) -> None:
        self.source = source
        self.after_callback = after
        self._playing = True
        self._paused = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self.stop_calls += 1
        self._playing = False
        self._paused = False

    async def move_to(self, channel: object) -> None:
        self.moved_to.append(channel)
        self.channel = channel

    async def disconnect(self, force: bool = False) -> None:
        self.disconnected = True
        self._playing = False


def _make_player(max_queue_size: int = 5, default_volume: int = 50) -> GuildPlayer:
    player = GuildPlayer(GUILD_ID, max_queue_size=max_queue_size, default_volume=default_volume)
    player._provider.create_audio_source = lambda track, volume: f"source:{track.title}:{volume}"  # type: ignore[method-assign]
    return player


def _attach_fake_voice_client(player: GuildPlayer, channel: object = "fake-channel") -> FakeVoiceClient:
    vc = FakeVoiceClient(channel)
    player.voice_client = vc
    return vc


# --- Queue management ---


def test_enqueue_and_queue_property() -> None:
    player = _make_player()
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))

    assert [t.title for t in player.queue] == ["A", "B"]


def test_enqueue_raises_when_full() -> None:
    player = _make_player(max_queue_size=1)
    player.enqueue(_track("A"))

    with pytest.raises(QueueFullError):
        player.enqueue(_track("B"))


def test_clear_queue() -> None:
    player = _make_player()
    player.enqueue(_track("A"))

    player.clear_queue()

    assert player.queue == []


def test_shuffle_preserves_all_items() -> None:
    player = _make_player(max_queue_size=20)
    for i in range(10):
        player.enqueue(_track(f"T{i}"))

    player.shuffle()

    assert {t.title for t in player.queue} == {f"T{i}" for i in range(10)}


# --- Volume ---


def test_set_volume_clamps_out_of_range() -> None:
    player = _make_player()

    player.set_volume(150)
    assert player.volume == 1.0

    player.set_volume(-10)
    assert player.volume == 0.0


class _FakeAudioSource(discord.AudioSource):
    def read(self) -> bytes:
        return b""


def test_set_volume_updates_live_transformer_source() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    vc.source = discord.PCMVolumeTransformer(_FakeAudioSource(), volume=0.5)

    player.set_volume(20)

    assert player.volume == pytest.approx(0.2)
    assert vc.source.volume == pytest.approx(0.2)


# --- Pause / resume ---


def test_pause_and_resume() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    vc._playing = True

    assert player.pause() is True
    assert vc.is_paused() is True
    assert player.resume() is True
    assert vc.is_paused() is False


def test_pause_returns_false_when_not_playing() -> None:
    player = _make_player()

    assert player.pause() is False


# --- Playback advance / loop modes ---


async def test_start_or_advance_plays_next_from_queue() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    player.enqueue(_track("A"))

    started = await player.start_or_advance()

    assert started is True
    assert player.current is not None
    assert player.current.title == "A"
    assert vc.source == "source:A:0.5"


async def test_start_or_advance_returns_false_when_empty() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)

    started = await player.start_or_advance()

    assert started is False
    assert player.current is None


async def test_loop_song_replays_current_track() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    await player.start_or_advance()
    player.set_loop(LoopMode.SONG)

    started = await player.start_or_advance()  # simulates track A finishing

    assert started is True
    assert player.current is not None
    assert player.current.title == "A"


async def test_loop_queue_requeues_finished_tracks() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))
    player.set_loop(LoopMode.QUEUE)

    await player.start_or_advance()  # playing A; B queued
    assert player.current.title == "A"

    await player.start_or_advance()  # A finishes -> requeue A, play B
    assert player.current.title == "B"
    assert [t.title for t in player.queue] == ["A"]

    await player.start_or_advance()  # B finishes -> requeue B, play A again
    assert player.current.title == "A"
    assert [t.title for t in player.queue] == ["B"]


async def test_skip_in_song_loop_advances_instead_of_repeating() -> None:
    """Regression test for the skip()/song-loop timing bug fixed this session."""
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))
    await player.start_or_advance()  # playing A
    player.set_loop(LoopMode.SONG)

    skipped = await player.skip()
    assert skipped is True
    assert vc.stop_calls == 1

    # Simulates the real discord.py `after` callback eventually firing
    # (deferred via run_coroutine_threadsafe in production - this test
    # drives the resulting start_or_advance() call directly, since that
    # deferred timing is exactly what made the original naive
    # toggle-then-restore approach not work - see GuildPlayer.skip()).
    started = await player.start_or_advance()

    assert started is True
    assert player.current.title == "B"  # advanced, did not repeat A


async def test_stop_clears_queue_and_loop_mode() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))
    player.set_loop(LoopMode.QUEUE)
    await player.start_or_advance()  # actually playing "A" now

    player.stop()

    assert player.queue == []
    assert player.loop_mode == LoopMode.OFF
    assert player.current is None
    assert vc.stop_calls == 1


def test_stop_is_a_noop_when_nothing_is_playing() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    player.enqueue(_track("A"))

    player.stop()  # nothing actually playing - should still clear state, just not call voice_client.stop()

    assert player.queue == []
    assert vc.stop_calls == 0


# --- Connect / disconnect ---


async def test_connect_reuses_existing_connection_to_same_channel() -> None:
    player = _make_player()
    channel = SimpleNamespace(id=1)
    vc = _attach_fake_voice_client(player, channel=channel)

    await player.connect(channel)

    assert vc.moved_to == []


async def test_connect_moves_to_a_different_channel() -> None:
    player = _make_player()
    _attach_fake_voice_client(player, channel=SimpleNamespace(id=1))
    new_channel = SimpleNamespace(id=2)

    await player.connect(new_channel)

    assert player.voice_client.moved_to == [new_channel]


async def test_connect_when_not_yet_connected() -> None:
    player = _make_player()
    created: dict[str, FakeVoiceClient] = {}

    class FakeChannel:
        id = 5

        async def connect(self) -> FakeVoiceClient:
            vc = FakeVoiceClient(channel=self)
            created["vc"] = vc
            return vc

    await player.connect(FakeChannel())

    assert player.voice_client is created["vc"]


async def test_disconnect_clears_all_state() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    await player.start_or_advance()

    await player.disconnect()

    assert player.voice_client is None
    assert player.current is None
    assert player.queue == []
    assert vc.disconnected is True


# --- remove_at ---


def test_remove_at_removes_and_returns_correct_track() -> None:
    player = _make_player(max_queue_size=20)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))
    player.enqueue(_track("C"))

    removed = player.remove_at(1)

    assert removed.title == "B"
    assert [t.title for t in player.queue] == ["A", "C"]


def test_remove_at_raises_for_out_of_range_index() -> None:
    player = _make_player()
    player.enqueue(_track("A"))

    with pytest.raises(IndexError):
        player.remove_at(5)


def test_remove_at_raises_for_negative_index() -> None:
    player = _make_player()
    player.enqueue(_track("A"))

    with pytest.raises(IndexError):
        player.remove_at(-1)


# --- Elapsed-time tracking ---


def _patch_monotonic(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Returns a single-item mutable list holding the fake clock's current
    value - tests advance time by mutating clock[0] directly."""
    clock = [0.0]
    monkeypatch.setattr("app.music.guild_player.time.monotonic", lambda: clock[0])
    return clock


def test_elapsed_seconds_is_none_before_anything_plays() -> None:
    player = _make_player()

    assert player.elapsed_seconds is None


async def test_elapsed_seconds_increases_after_start_or_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _patch_monotonic(monkeypatch)
    player = _make_player()
    _attach_fake_voice_client(player)
    player.enqueue(_track("A"))

    await player.start_or_advance()
    clock[0] += 10

    assert player.elapsed_seconds == pytest.approx(10.0)


async def test_elapsed_seconds_freezes_while_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _patch_monotonic(monkeypatch)
    player = _make_player()
    _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    await player.start_or_advance()

    clock[0] += 5
    assert player.pause() is True
    assert player.elapsed_seconds == pytest.approx(5.0)

    clock[0] += 100  # time passes while paused - must not count
    assert player.elapsed_seconds == pytest.approx(5.0)

    assert player.resume() is True
    clock[0] += 3
    assert player.elapsed_seconds == pytest.approx(8.0)  # 5 before pause + 3 after resume


async def test_elapsed_seconds_resets_on_advance_to_next_track(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _patch_monotonic(monkeypatch)
    player = _make_player()
    _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))

    await player.start_or_advance()  # playing A
    clock[0] += 20
    await player.start_or_advance()  # A finishes, B starts

    assert player.current.title == "B"
    assert player.elapsed_seconds == pytest.approx(0.0)


def test_elapsed_seconds_is_none_after_stop() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)
    player.current = _track("A")
    player.current_started_at = 0.0

    player.stop()

    assert player.elapsed_seconds is None


# --- Restart (the dashboard's |◄ PREV) ---


async def test_restart_replays_current_track_without_touching_the_queue() -> None:
    player = _make_player()
    vc = _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))
    await player.start_or_advance()  # playing A, B queued

    restarted = await player.restart()
    assert restarted is True
    assert vc.stop_calls == 1

    # The deferred `after` callback's start_or_advance(), driven directly.
    await player.start_or_advance()

    assert player.current.title == "A"
    assert [t.title for t in player.queue] == ["B"]


async def test_restart_in_queue_loop_does_not_duplicate_the_track() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)
    player.enqueue(_track("A"))
    player.enqueue(_track("B"))
    await player.start_or_advance()
    player.set_loop(LoopMode.QUEUE)

    await player.restart()
    await player.start_or_advance()

    assert player.current.title == "A"
    assert [t.title for t in player.queue] == ["B"]


async def test_restart_with_nothing_playing_is_false() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)

    assert await player.restart() is False


async def test_on_track_start_fires_for_each_started_track() -> None:
    player = _make_player()
    _attach_fake_voice_client(player)
    started: list[str] = []

    async def hook(track: Track) -> None:
        started.append(track.title)

    player.on_track_start = hook
    player.enqueue(_track("A"))
    await player.start_or_advance()
    await asyncio.sleep(0)  # the hook runs as a background task

    assert started == ["A"]
