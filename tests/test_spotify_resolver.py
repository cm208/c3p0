"""Tests for Spotify link -> YouTube-search-query resolution.

httpx.MockTransport stands in for the real network call to Spotify's
public embed page - no real request ever leaves this test.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.music.spotify_resolver import (
    SpotifyResolutionError,
    is_spotify_url,
    resolve_spotify_track,
)


def _embed_html(entity: dict) -> str:
    next_data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script></body></html>'


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- is_spotify_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123",
        "open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX",
        "https://open.spotify.com/intl-en/track/0VjIjW4GlUZAMYd2vXMi3b",
    ],
)
def test_is_spotify_url_true_for_track_playlist_album(url: str) -> None:
    assert is_spotify_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "never gonna give you up",
        "https://open.spotify.com/artist/1Xyo4u8uXC1ZmMpatF05PJ",  # not track/playlist/album
    ],
)
def test_is_spotify_url_false_for_non_matching(url: str) -> None:
    assert is_spotify_url(url) is False


# --- resolve_spotify_track: track ---


async def test_resolve_track_returns_title_and_artists() -> None:
    entity = {"type": "track", "name": "Blinding Lights", "artists": [{"name": "The Weeknd"}]}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "open.spotify.com/embed/track/0VjIjW4GlUZAMYd2vXMi3b" in str(request.url)
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        ref = await resolve_spotify_track(http, "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b")

    assert ref.title == "Blinding Lights"
    assert ref.artists == ("The Weeknd",)
    assert ref.search_query == "Blinding Lights The Weeknd"


async def test_resolve_track_with_multiple_artists() -> None:
    entity = {"type": "track", "name": "Song", "artists": [{"name": "A"}, {"name": "B"}]}

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        ref = await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")

    assert ref.artists == ("A", "B")
    assert ref.search_query == "Song A B"


# --- resolve_spotify_track: playlist/album (first entry only) ---


async def test_resolve_playlist_uses_first_track_list_entry() -> None:
    entity = {
        "type": "playlist",
        "name": "Today's Top Hits",
        "trackList": [
            {"title": "First Song", "subtitle": "Artist One, Artist Two"},
            {"title": "Second Song", "subtitle": "Someone Else"},
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/embed/playlist/" in str(request.url)
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        ref = await resolve_spotify_track(http, "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")

    assert ref.title == "First Song"
    assert ref.artists == ("Artist One", "Artist Two")


async def test_resolve_album_uses_first_track_list_entry() -> None:
    entity = {
        "type": "album",
        "name": "An Album",
        "trackList": [{"title": "Opening Track", "subtitle": "The Band"}],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/embed/album/" in str(request.url)
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        ref = await resolve_spotify_track(http, "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX")

    assert ref.title == "Opening Track"
    assert ref.artists == ("The Band",)


async def test_resolve_playlist_with_no_tracks_raises() -> None:
    entity = {"type": "playlist", "name": "Empty", "trackList": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="no tracks"):
            await resolve_spotify_track(http, "https://open.spotify.com/playlist/abc")


# --- Error paths ---


async def test_resolve_raises_for_non_spotify_url() -> None:
    async with _client(lambda r: httpx.Response(200)) as http:
        with pytest.raises(SpotifyResolutionError, match="doesn't look like"):
            await resolve_spotify_track(http, "https://example.com/not-spotify")


async def test_resolve_raises_for_unsupported_entity_type() -> None:
    entity = {"type": "episode", "name": "A Podcast Episode"}

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="track, playlist, or album"):
            await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")


async def test_resolve_raises_on_non_200_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="rejected that link"):
            await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")


async def test_resolve_raises_when_next_data_script_missing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>no data here</body></html>")

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="Couldn't read"):
            await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")


async def test_resolve_raises_when_entity_missing_from_payload() -> None:
    bad_html = '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=bad_html)

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="Couldn't read"):
            await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")


async def test_resolve_raises_on_network_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="Couldn't reach Spotify"):
            await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")


async def test_resolve_raises_when_track_has_no_title() -> None:
    entity = {"type": "track", "name": "", "artists": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_embed_html(entity))

    async with _client(handler) as http:
        with pytest.raises(SpotifyResolutionError, match="Couldn't read that Spotify track's title"):
            await resolve_spotify_track(http, "https://open.spotify.com/track/abc123")
