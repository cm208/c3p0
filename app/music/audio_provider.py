"""Resolves a user-supplied URL or search query into playable audio.

This is the one place in the codebase that shells out to an external tool
(yt-dlp, which in turn invokes ffmpeg) for arbitrary user-supplied input,
and the only place that makes outbound network requests based on it -
kept behind this narrow interface so the resolution strategy can change
(or be hardened further) without touching GuildPlayer/MusicService/MusicCog.

Deliberately NOT discord.py-free like app/services/*.py - constructing the
actual playable audio source (create_audio_source) is inherently a
discord.py concern (FFmpegPCMAudio/PCMVolumeTransformer), and this layer
sits below GuildPlayer, closer to Discord/ffmpeg than the DB-backed
services are.

Playlists are deliberately NOT expanded - yt-dlp is called with
noplaylist=True, so a playlist link resolves to just its first entry. Full
playlist queueing is a known gap, left for a follow-up so a mistyped/
oversized playlist link can't flood a guild's queue. A Spotify link is resolved
first (see app/music/spotify_resolver.py) into a "<title> <artists>"
string, which then flows through this same yt-dlp search path as an
ordinary text query - Spotify playlists/albums get the identical
first-entry-only treatment as a YouTube playlist link, for the same
reason.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord
import httpx
import yt_dlp

from app.music.spotify_resolver import SpotifyResolutionError, is_spotify_url, resolve_spotify_track

_YTDLP_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",  # noqa: S104 - yt-dlp's own IPv4-preference workaround, not a bind address
}

# Reconnect flags: audio streams occasionally drop mid-play; retry rather
# than silently going quiet.
_FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_FFMPEG_OPTIONS = "-vn"


class AudioResolutionError(Exception):
    """Raised when a query/URL can't be resolved to playable audio. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class Track:
    title: str
    stream_url: str
    webpage_url: str
    duration_seconds: int | None
    requested_by: int


class AudioProvider:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        # Only ever used for the Spotify-embed-page lookup below - yt-dlp
        # does its own networking. Lazily-owned rather than threaded in
        # from app/bot.py: a GuildPlayer (and the AudioProvider it owns) is
        # already a per-guild, request-independent, long-lived object, so
        # one client per provider is the natural lifetime match, and tests
        # can still inject one wired to httpx.MockTransport via this param.
        self._http = http or httpx.AsyncClient()

    async def resolve(self, query: str, *, requested_by: int) -> Track:
        if is_spotify_url(query):
            try:
                spotify_track = await resolve_spotify_track(self._http, query)
            except SpotifyResolutionError as exc:
                raise AudioResolutionError(str(exc)) from exc
            query = f"ytsearch1:{spotify_track.search_query}"

        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, self._extract, query)
        except yt_dlp.utils.DownloadError as exc:
            raise AudioResolutionError(
                "Couldn't find or access that - it may be private, deleted, or unsupported."
            ) from exc
        except Exception as exc:
            raise AudioResolutionError("Something went wrong resolving that track.") from exc

        if data is None:
            raise AudioResolutionError("No results found.")

        if "entries" in data:
            entries = [entry for entry in data["entries"] if entry]
            if not entries:
                raise AudioResolutionError("No results found.")
            data = entries[0]

        stream_url = data.get("url")
        if not stream_url:
            raise AudioResolutionError("Couldn't find a playable audio stream for that.")

        return Track(
            title=data.get("title") or "Unknown title",
            stream_url=stream_url,
            webpage_url=data.get("webpage_url") or query,
            duration_seconds=data.get("duration"),
            requested_by=requested_by,
        )

    def _extract(self, query: str) -> dict:
        with yt_dlp.YoutubeDL(_YTDLP_OPTIONS) as ydl:
            return ydl.extract_info(query, download=False)

    def create_audio_source(self, track: Track, *, volume: float) -> discord.PCMVolumeTransformer:
        source = discord.FFmpegPCMAudio(
            track.stream_url, before_options=_FFMPEG_BEFORE_OPTIONS, options=_FFMPEG_OPTIONS
        )
        return discord.PCMVolumeTransformer(source, volume=volume)
