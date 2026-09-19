"""AudioProvider tests. yt-dlp and ffmpeg are fully mocked here - no real
network calls, and no dependency on ffmpeg actually being installed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
import yt_dlp

from app.music.audio_provider import AudioProvider, AudioResolutionError, Track
from app.music.spotify_resolver import SpotifyResolutionError, SpotifyTrackRef


def _fake_ydl(extract_result: object = None, *, side_effect: Exception | None = None) -> MagicMock:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.__exit__.return_value = False
    if side_effect is not None:
        ydl.extract_info.side_effect = side_effect
    else:
        ydl.extract_info.return_value = extract_result
    return ydl


async def test_resolve_returns_track_from_direct_result() -> None:
    provider = AudioProvider()
    data = {"title": "Song", "url": "http://stream", "webpage_url": "http://page", "duration": 120}

    with patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=_fake_ydl(data)):
        track = await provider.resolve("http://page", requested_by=1)

    assert track.title == "Song"
    assert track.stream_url == "http://stream"
    assert track.duration_seconds == 120
    assert track.requested_by == 1


async def test_resolve_uses_first_search_entry() -> None:
    provider = AudioProvider()
    data = {"entries": [{"title": "First", "url": "http://s1", "webpage_url": "http://p1"}]}

    with patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=_fake_ydl(data)):
        track = await provider.resolve("some search", requested_by=1)

    assert track.title == "First"


async def test_resolve_raises_on_empty_search_entries() -> None:
    provider = AudioProvider()
    data = {"entries": [None, None]}

    with patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=_fake_ydl(data)):
        with pytest.raises(AudioResolutionError, match="No results"):
            await provider.resolve("nothing", requested_by=1)


async def test_resolve_raises_when_no_stream_url() -> None:
    provider = AudioProvider()
    data = {"title": "Song"}

    with patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=_fake_ydl(data)):
        with pytest.raises(AudioResolutionError, match="playable audio stream"):
            await provider.resolve("query", requested_by=1)


async def test_resolve_wraps_download_error() -> None:
    provider = AudioProvider()
    fake = _fake_ydl(side_effect=yt_dlp.utils.DownloadError("nope"))

    with patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=fake):
        with pytest.raises(AudioResolutionError, match="private, deleted, or unsupported"):
            await provider.resolve("query", requested_by=1)


async def test_resolve_wraps_unexpected_error() -> None:
    provider = AudioProvider()
    fake = _fake_ydl(side_effect=RuntimeError("boom"))

    with patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=fake):
        with pytest.raises(AudioResolutionError, match="Something went wrong"):
            await provider.resolve("query", requested_by=1)


# --- Spotify link resolution ---


async def test_resolve_spotify_url_searches_youtube_by_title_and_artist() -> None:
    provider = AudioProvider()
    data = {"title": "Blinding Lights", "url": "http://stream", "webpage_url": "http://page"}
    captured_query: dict[str, str] = {}

    def _fake_extract(self: AudioProvider, query: str) -> dict:
        captured_query["query"] = query
        return data

    with (
        patch(
            "app.music.audio_provider.resolve_spotify_track",
            AsyncMock(return_value=SpotifyTrackRef(title="Blinding Lights", artists=("The Weeknd",))),
        ),
        patch.object(AudioProvider, "_extract", _fake_extract),
    ):
        track = await provider.resolve("https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b", requested_by=1)

    assert captured_query["query"] == "ytsearch1:Blinding Lights The Weeknd"
    assert track.title == "Blinding Lights"


async def test_resolve_spotify_failure_becomes_audio_resolution_error() -> None:
    provider = AudioProvider()

    with patch(
        "app.music.audio_provider.resolve_spotify_track",
        AsyncMock(side_effect=SpotifyResolutionError("That Spotify link isn't playable.")),
    ):
        with pytest.raises(AudioResolutionError, match="isn't playable"):
            await provider.resolve("https://open.spotify.com/track/abc123", requested_by=1)


async def test_resolve_non_spotify_query_never_calls_spotify_resolver() -> None:
    provider = AudioProvider()
    data = {"title": "Song", "url": "http://stream", "webpage_url": "http://page"}

    with (
        patch("app.music.audio_provider.resolve_spotify_track", AsyncMock()) as mock_resolve,
        patch("app.music.audio_provider.yt_dlp.YoutubeDL", return_value=_fake_ydl(data)),
    ):
        await provider.resolve("some plain search", requested_by=1)

    mock_resolve.assert_not_called()


class _FakeAudioSource(discord.AudioSource):
    """Minimal real AudioSource - PCMVolumeTransformer's __init__ requires
    isinstance(original, discord.AudioSource), so a plain object() won't do."""

    def read(self) -> bytes:
        return b""


def test_create_audio_source_wraps_ffmpeg_with_volume() -> None:
    provider = AudioProvider()
    track = Track(title="T", stream_url="http://s", webpage_url="http://p", duration_seconds=None, requested_by=1)

    with patch(
        "app.music.audio_provider.discord.FFmpegPCMAudio", return_value=_FakeAudioSource()
    ) as mock_ffmpeg:
        result = provider.create_audio_source(track, volume=0.4)

    mock_ffmpeg.assert_called_once()
    assert isinstance(result, discord.PCMVolumeTransformer)
    assert result.volume == pytest.approx(0.4)
