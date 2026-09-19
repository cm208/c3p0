"""Per-guild playback state - queue, current track, volume, loop mode.

Not a discord-free "service" the way the rest of the codebase uses that
word - a live audio player is inherently tied to a discord.VoiceClient,
and abstracting that away would just move the same coupling one file over
for no benefit. See app/music/audio_provider.py's module docstring - the
layering is MusicService -> GuildPlayer -> AudioProvider, moving closer to
Discord/ffmpeg at each step.

Not persisted - restarting the bot clears all playback state by design;
music queues are runtime-only, not something worth the complexity of
surviving a restart.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from enum import Enum

import discord

from app.music.audio_provider import AudioProvider, Track

logger = logging.getLogger(__name__)


class LoopMode(Enum):
    OFF = "off"
    SONG = "song"
    QUEUE = "queue"


class QueueFullError(Exception):
    """Raised when enqueueing would exceed max_queue_size. Message is user-safe."""


class GuildPlayer:
    def __init__(self, guild_id: int, *, max_queue_size: int, default_volume: int) -> None:
        self.guild_id = guild_id
        self.max_queue_size = max_queue_size
        self.volume = _clamp_percent(default_volume) / 100
        self.loop_mode = LoopMode.OFF
        self.voice_client: discord.VoiceClient | None = None
        self.current: Track | None = None
        self._queue: deque[Track] = deque()
        self._provider = AudioProvider()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Consumed by the next start_or_advance() call only - see skip().
        self._skip_song_loop_once = False
        # Elapsed-time tracking for `current`, correct across pause/resume -
        # see elapsed_seconds. All three reset together whenever a new track
        # actually starts (start_or_advance) or playback stops (stop()).
        self.current_started_at: float | None = None
        self._paused_at: float | None = None
        self._paused_accumulated: float = 0.0

    @property
    def queue(self) -> list[Track]:
        return list(self._queue)

    @property
    def elapsed_seconds(self) -> float | None:
        """Seconds of actual playback time for `current`, excluding time spent
        paused. None if nothing is currently playing."""
        if self.current is None or self.current_started_at is None:
            return None
        now = time.monotonic()
        still_paused = (now - self._paused_at) if self._paused_at is not None else 0.0
        return now - self.current_started_at - self._paused_accumulated - still_paused

    def is_connected(self) -> bool:
        return self.voice_client is not None and self.voice_client.is_connected()

    def is_playing(self) -> bool:
        return self.voice_client is not None and (
            self.voice_client.is_playing() or self.voice_client.is_paused()
        )

    async def connect(self, channel: discord.VoiceChannel) -> None:
        self._loop = asyncio.get_running_loop()
        if self.voice_client is not None and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                await self.voice_client.move_to(channel)
            return
        self.voice_client = await channel.connect()

    async def disconnect(self) -> None:
        if self.voice_client is not None:
            await self.voice_client.disconnect(force=True)
        self.voice_client = None
        self.current = None
        self._queue.clear()

    def enqueue(self, track: Track) -> int:
        """Returns the resulting queue length. Raises QueueFullError if at capacity."""
        if len(self._queue) >= self.max_queue_size:
            raise QueueFullError(f"Queue is full (max {self.max_queue_size}).")
        self._queue.append(track)
        return len(self._queue)

    async def resolve_and_enqueue(self, query: str, *, requested_by: int) -> tuple[Track, int]:
        """Resolve `query` via the AudioProvider and add it to the queue.

        Returns (track, resulting_queue_length). Raises AudioResolutionError
        (bad/unsupported query) or QueueFullError (at capacity) - the caller
        (MusicCog) turns either into a user-facing message.
        """
        track = await self._provider.resolve(query, requested_by=requested_by)
        position = self.enqueue(track)
        return track, position

    def clear_queue(self) -> None:
        self._queue.clear()

    def remove_at(self, index: int) -> Track:
        """Remove and return the upcoming-queue entry at `index` (0-based).

        Raises IndexError if out of range - callers should treat this as
        "that item isn't there anymore" (e.g. a stale index from a page that
        was open while the queue changed), not a bug.
        """
        if index < 0:
            raise IndexError(index)
        items = list(self._queue)
        track = items.pop(index)
        self._queue = deque(items)
        return track

    def shuffle(self) -> None:
        items = list(self._queue)
        random.shuffle(items)
        self._queue = deque(items)

    def set_loop(self, mode: LoopMode) -> None:
        self.loop_mode = mode

    def set_volume(self, percent: int) -> None:
        self.volume = _clamp_percent(percent) / 100
        if self.voice_client is not None and isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
            self.voice_client.source.volume = self.volume

    def pause(self) -> bool:
        if self.voice_client is not None and self.voice_client.is_playing():
            self.voice_client.pause()
            self._paused_at = time.monotonic()
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client is not None and self.voice_client.is_paused():
            self.voice_client.resume()
            if self._paused_at is not None:
                self._paused_accumulated += time.monotonic() - self._paused_at
                self._paused_at = None
            return True
        return False

    def stop(self) -> None:
        """Stop playback and clear the queue - does not disconnect."""
        self.clear_queue()
        self.loop_mode = LoopMode.OFF
        if self.voice_client is not None and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()  # triggers the `after` callback, which finds an empty queue and stops
        self.current = None
        self.current_started_at = None
        self._paused_at = None
        self._paused_accumulated = 0.0

    async def skip(self) -> bool:
        """Stop the current track so playback advances to the next one immediately."""
        if self.voice_client is None or not (self.voice_client.is_playing() or self.voice_client.is_paused()):
            return False
        # In song-loop mode, skipping should still move on rather than repeat
        # the current track. voice_client.stop() only *schedules* the after
        # callback (run_coroutine_threadsafe back onto the event loop) - it
        # doesn't run synchronously - so a toggle-then-restore of loop_mode
        # around this call would already be undone before start_or_advance()
        # ever sees it. A one-shot flag consumed there is the actual fix.
        if self.loop_mode == LoopMode.SONG:
            self._skip_song_loop_once = True
        self.voice_client.stop()
        return True

    async def start_or_advance(self) -> bool:
        """Play the next track. Returns False if there's nothing to play."""
        skip_song_loop = self._skip_song_loop_once
        self._skip_song_loop_once = False

        if self.loop_mode == LoopMode.SONG and self.current is not None and not skip_song_loop:
            next_track = self.current
        elif self._queue:
            next_track = self._queue.popleft()
            if self.loop_mode == LoopMode.QUEUE and self.current is not None:
                self._queue.append(self.current)
        else:
            self.current = None
            return False

        self.current = next_track
        self.current_started_at = time.monotonic()
        self._paused_at = None
        self._paused_accumulated = 0.0
        source = self._provider.create_audio_source(next_track, volume=self.volume)

        loop = self._loop or asyncio.get_running_loop()

        def _after(error: Exception | None) -> None:
            if error:
                logger.error("Playback error", exc_info=error, extra={"guild_id": self.guild_id})
            asyncio.run_coroutine_threadsafe(self._advance(), loop)

        assert self.voice_client is not None
        self.voice_client.play(source, after=_after)
        return True

    async def _advance(self) -> None:
        try:
            await self.start_or_advance()
        except Exception:
            logger.exception("Failed to advance playback", extra={"guild_id": self.guild_id})


def _clamp_percent(value: int) -> int:
    return max(0, min(100, value))
