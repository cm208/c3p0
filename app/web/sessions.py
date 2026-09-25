"""Server-side dashboard sessions.

Deliberately not using Starlette's SessionMiddleware, which signs a
client-side cookie: that would bake the user's guild permissions into a
blob the client holds and the server trusts until it expires. Permissions
can be revoked in Discord at any moment, so this needs to be re-checked
against a short TTL cache instead - which means the session itself must
live server-side, keyed by an opaque cookie value the client can't read
or forge.

Not under app/services/ - like app/web/discord_client.py, this is
web-specific plumbing the Discord bot process never calls.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.db.database import session_scope
from app.db.repositories.web_session_repository import WebSessionRepository
from app.web import discord_client
from app.web.config import WebConfig
from app.web.discord_client import DiscordUser, OAuthTokens

SESSION_COOKIE_NAME = "c3p0_session"

# How much of a head start to give an about-to-expire access token before
# actually needing it, so a refresh has time to complete first.
_TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _as_utc(dt: datetime) -> datetime:
    """SQLite silently drops tzinfo on round-trip, even for a
    DateTime(timezone=True) column - values come back naive, always in
    whatever timezone they were stored in (UTC, per this module's own
    writes). Re-attach it before comparing against a tz-aware
    datetime.now(UTC), or the comparison raises TypeError.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LoadedSession:
    id: int
    discord_user_id: int
    discord_username: str | None
    discord_avatar_hash: str | None
    csrf_token: str
    # guild_id -> permissions bitfield, for every guild the user is in as
    # of the last refresh (see SessionService.load's TTL logic).
    guild_permissions: dict[int, int]


class SessionService:
    def __init__(self, config: WebConfig) -> None:
        self._config = config

    async def create_session(self, *, tokens: OAuthTokens, user: DiscordUser) -> str:
        """Persist a new session and return the raw cookie value."""
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        async with session_scope() as db:
            repo = WebSessionRepository(db)
            await repo.create(
                token_hash=_hash_token(raw_token),
                discord_user_id=user.id,
                discord_username=user.username,
                discord_avatar_hash=user.avatar,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                token_expires_at=now + timedelta(seconds=tokens.expires_in),
                csrf_token=secrets.token_urlsafe(24),
                expires_at=now + timedelta(days=self._config.session_ttl_days),
            )
        return raw_token

    async def load(
        self, raw_token: str | None, *, http: httpx.AsyncClient
    ) -> LoadedSession | None:
        """Load a session by its cookie value, refreshing Discord state if stale.

        Returns None for a missing, expired, or no-longer-valid (Discord
        rejected the refresh - e.g. the user revoked access) session.
        """
        if not raw_token:
            return None

        now = datetime.now(UTC)
        async with session_scope() as db:
            repo = WebSessionRepository(db)
            record = await repo.get_by_token_hash(_hash_token(raw_token))
            if record is None:
                return None
            if _as_utc(record.expires_at) < now:
                await repo.delete(record.id)
                return None

            cache = record.guild_permissions_cache
            cached_at = record.guild_permissions_cached_at
            stale = (
                cache is None
                or cached_at is None
                or (now - _as_utc(cached_at))
                > timedelta(seconds=self._config.guild_permissions_cache_ttl_seconds)
            )

            if stale:
                access_token = record.access_token
                try:
                    if _as_utc(record.token_expires_at) <= now + _TOKEN_REFRESH_BUFFER:
                        tokens = await discord_client.refresh_access_token(
                            http,
                            client_id=self._config.discord_client_id,
                            client_secret=self._config.discord_client_secret,
                            refresh_token=record.refresh_token,
                        )
                        access_token = tokens.access_token
                        await repo.update_tokens(
                            record.id,
                            access_token=tokens.access_token,
                            refresh_token=tokens.refresh_token,
                            token_expires_at=now + timedelta(seconds=tokens.expires_in),
                        )

                    guilds = await discord_client.fetch_user_guilds(http, access_token)
                    cache = {str(g.id): g.permissions for g in guilds}
                    await repo.update_guild_permissions_cache(record.id, cache=cache, cached_at=now)
                except discord_client.DiscordAPIError:
                    # Refresh token revoked, or Discord otherwise rejected
                    # us - the session can't be trusted any further.
                    await repo.delete(record.id)
                    return None

            await repo.touch(record.id, now=now)

            return LoadedSession(
                id=record.id,
                discord_user_id=record.discord_user_id,
                discord_username=record.discord_username,
                discord_avatar_hash=record.discord_avatar_hash,
                csrf_token=record.csrf_token,
                guild_permissions={int(k): v for k, v in (cache or {}).items()},
            )

    async def fetch_guild_summaries(
        self, session: LoadedSession, *, http: httpx.AsyncClient
    ) -> list[discord_client.DiscordUserGuild] | None:
        """The operator's guilds with names and approximate member/online
        counts, for the guild picker. Uses the access token load() already
        kept fresh; returns None (callers fall back to names only) if
        Discord refuses or rate-limits the call, rather than ending the
        session the way a failed permissions refresh does."""
        async with session_scope() as db:
            record = await WebSessionRepository(db).get(session.id)
            if record is None:
                return None
            access_token = record.access_token
        try:
            return await discord_client.fetch_user_guilds(http, access_token, with_counts=True)
        except (discord_client.DiscordAPIError, httpx.HTTPError):
            return None

    async def delete(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        async with session_scope() as db:
            repo = WebSessionRepository(db)
            record = await repo.get_by_token_hash(_hash_token(raw_token))
            if record is not None:
                await repo.delete(record.id)
