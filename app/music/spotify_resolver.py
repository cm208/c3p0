"""Turn a Spotify track/playlist/album link into a YouTube search query.

Spotify doesn't provide a stream any third-party bot can legally play, so
a Spotify link is only ever treated as a metadata reference, resolved
through a supported audio source (never its actual audio). This module
only ever reads public track/artist *names* off Spotify's own embed page,
never touches playback.

Deliberately uses Spotify's embed page (https://open.spotify.com/embed/
<kind>/<id> - the same page the public, credential-free oEmbed endpoint's
own `iframe_url` points third-party embedders at) rather than the real
Spotify Web API. The Web API needs a registered app + client credentials
only the server operator can obtain; the embed page needs nothing and
carries a richer `__NEXT_DATA__` JSON payload than oEmbed's response
(oEmbed's own `title` field is bare - no artist - which makes it useless
for building an accurate YouTube search on its own).

Playlist/album links resolve to their first track only, deliberately
mirroring audio_provider.py's `noplaylist=True` behavior for a YouTube
playlist link - not a new multi-enqueue mechanism, just consistent with
the one that already exists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

_SPOTIFY_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?(track|playlist|album)/([A-Za-z0-9]+)"
)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# A plain, honest browser User-Agent - this is the same public embed page
# Spotify's own oEmbed iframe_url directs arbitrary third-party embedders
# to fetch, not a gated or internal endpoint.
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
_REQUEST_TIMEOUT = 10.0


class SpotifyResolutionError(Exception):
    """Raised when a Spotify URL can't be turned into a search query. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class SpotifyTrackRef:
    title: str
    artists: tuple[str, ...]

    @property
    def search_query(self) -> str:
        return f"{self.title} {' '.join(self.artists)}".strip()


def is_spotify_url(query: str) -> bool:
    return _SPOTIFY_URL_RE.search(query) is not None


async def resolve_spotify_track(http: httpx.AsyncClient, query: str) -> SpotifyTrackRef:
    """Resolve a Spotify track/playlist/album URL to a (title, artists) ref
    suitable for handing to a YouTube search. Raises SpotifyResolutionError
    on anything that isn't a clean success."""
    match = _SPOTIFY_URL_RE.search(query)
    if match is None:
        raise SpotifyResolutionError("That doesn't look like a Spotify track, playlist, or album link.")
    kind, spotify_id = match.group(1), match.group(2)

    try:
        response = await http.get(
            f"https://open.spotify.com/embed/{kind}/{spotify_id}",
            headers=_REQUEST_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise SpotifyResolutionError("Couldn't reach Spotify to resolve that link.") from exc
    if response.status_code != 200:
        raise SpotifyResolutionError("Spotify rejected that link - it may be private, deleted, or region-locked.")

    data_match = _NEXT_DATA_RE.search(response.text)
    if data_match is None:
        raise SpotifyResolutionError("Couldn't read that Spotify link's details.")
    try:
        payload = json.loads(data_match.group(1))
        entity = payload["props"]["pageProps"]["state"]["data"]["entity"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SpotifyResolutionError("Couldn't read that Spotify link's details.") from exc

    entity_type = entity.get("type")
    if entity_type == "track":
        return _track_ref_from_track_entity(entity)
    if entity_type in ("playlist", "album"):
        track_list = entity.get("trackList") or []
        if not track_list:
            raise SpotifyResolutionError("That Spotify playlist/album has no tracks.")
        return _track_ref_from_track_list_item(track_list[0])
    raise SpotifyResolutionError("That Spotify link isn't a track, playlist, or album C3P0 can play.")


def _track_ref_from_track_entity(entity: dict) -> SpotifyTrackRef:
    title = entity.get("name") or ""
    artists = tuple(a["name"] for a in entity.get("artists", []) if a.get("name"))
    if not title:
        raise SpotifyResolutionError("Couldn't read that Spotify track's title.")
    return SpotifyTrackRef(title=title, artists=artists)


def _track_ref_from_track_list_item(item: dict) -> SpotifyTrackRef:
    title = item.get("title") or ""
    subtitle = item.get("subtitle") or ""
    artists = tuple(a.strip() for a in subtitle.split(",") if a.strip())
    if not title:
        raise SpotifyResolutionError("Couldn't read that Spotify track's title.")
    return SpotifyTrackRef(title=title, artists=artists)
