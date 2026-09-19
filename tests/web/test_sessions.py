from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.web_session_repository import WebSessionRepository
from app.web.config import WebConfig
from app.web.discord_client import DiscordUser, OAuthTokens
from app.web.sessions import SessionService, _hash_token


def _config(**overrides) -> WebConfig:
    defaults: dict = {
        "discord_client_id": 123,
        "discord_client_secret": "secret",
        "discord_bot_token": "bot-token",
        "public_base_url": "https://example.com",
        "internal_api_token": "internal-secret",
        "guild_permissions_cache_ttl_seconds": 3600,
    }
    defaults.update(overrides)
    return WebConfig(**defaults)


def _mock_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _guilds_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json=[{"id": "111", "name": "Guild", "icon": None, "permissions": "32"}]
    )


async def test_create_then_load_round_trip(db_session: AsyncSession) -> None:
    service = SessionService(_config())
    tokens = OAuthTokens(access_token="at", refresh_token="rt", expires_in=604800)
    user = DiscordUser(id=42, username="volvo", avatar="hash")

    raw_token = await service.create_session(tokens=tokens, user=user)

    async with _mock_http(_guilds_response) as http:
        loaded = await service.load(raw_token, http=http)

    assert loaded is not None
    assert loaded.discord_user_id == 42
    assert loaded.discord_username == "volvo"
    assert loaded.discord_avatar_hash == "hash"
    assert loaded.guild_permissions == {111: 32}


async def test_load_returns_none_for_missing_cookie(db_session: AsyncSession) -> None:
    service = SessionService(_config())

    async with _mock_http(_guilds_response) as http:
        assert await service.load(None, http=http) is None


async def test_load_returns_none_for_unknown_token(db_session: AsyncSession) -> None:
    service = SessionService(_config())

    async with _mock_http(_guilds_response) as http:
        assert await service.load("does-not-exist", http=http) is None


async def _seed_session(
    db_session: AsyncSession,
    *,
    raw_token: str = "raw-token",
    token_expires_at: datetime | None = None,
    expires_at: datetime | None = None,
    guild_permissions_cache: dict | None = None,
    guild_permissions_cached_at: datetime | None = None,
) -> None:
    now = datetime.now(UTC)
    repo = WebSessionRepository(db_session)
    record = await repo.create(
        token_hash=_hash_token(raw_token),
        discord_user_id=42,
        discord_username="volvo",
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=token_expires_at or now + timedelta(days=7),
        csrf_token="csrf-token",
        expires_at=expires_at or now + timedelta(days=30),
    )
    if guild_permissions_cache is not None:
        record.guild_permissions_cache = guild_permissions_cache
        record.guild_permissions_cached_at = guild_permissions_cached_at or now
        await db_session.flush()


async def test_load_deletes_and_returns_none_for_expired_session(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _seed_session(db_session, expires_at=now - timedelta(days=1))

    service = SessionService(_config())
    async with _mock_http(_guilds_response) as http:
        loaded = await service.load("raw-token", http=http)

    assert loaded is None


async def test_load_uses_fresh_cache_without_calling_discord(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _seed_session(
        db_session,
        guild_permissions_cache={"999": 8},
        guild_permissions_cached_at=now,
    )

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    service = SessionService(_config(guild_permissions_cache_ttl_seconds=3600))
    async with _mock_http(fail_if_called) as http:
        loaded = await service.load("raw-token", http=http)

    assert loaded is not None
    assert loaded.guild_permissions == {999: 8}


async def test_load_refetches_when_cache_stale(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _seed_session(
        db_session,
        guild_permissions_cache={"111": 1},
        guild_permissions_cached_at=now - timedelta(hours=1),
    )

    service = SessionService(_config(guild_permissions_cache_ttl_seconds=1))
    async with _mock_http(_guilds_response) as http:
        loaded = await service.load("raw-token", http=http)

    assert loaded is not None
    assert loaded.guild_permissions == {111: 32}


async def test_load_refreshes_access_token_when_near_expiry(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _seed_session(db_session, token_expires_at=now)  # already at/past expiry

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200, json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 604800}
            )
        return httpx.Response(200, json=[{"id": "111", "name": "G", "icon": None, "permissions": "32"}])

    service = SessionService(_config())
    async with _mock_http(handler) as http:
        loaded = await service.load("raw-token", http=http)

    assert loaded is not None
    assert any(path.endswith("/oauth2/token") for path in calls)
    assert any(path.endswith("/guilds") for path in calls)


async def test_load_deletes_session_when_refresh_rejected(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _seed_session(db_session, token_expires_at=now)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    service = SessionService(_config())
    async with _mock_http(handler) as http:
        loaded = await service.load("raw-token", http=http)

    assert loaded is None

    # And the session is actually gone, not just rejected this once.
    repo = WebSessionRepository(db_session)
    assert await repo.get_by_token_hash(_hash_token("raw-token")) is None


async def test_delete_removes_session(db_session: AsyncSession) -> None:
    await _seed_session(db_session)
    service = SessionService(_config())

    await service.delete("raw-token")

    async with _mock_http(_guilds_response) as http:
        assert await service.load("raw-token", http=http) is None


async def test_delete_is_a_noop_for_missing_cookie(db_session: AsyncSession) -> None:
    service = SessionService(_config())

    await service.delete(None)  # should not raise
