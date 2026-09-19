from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.db.repositories.web_session_repository import WebSessionRepository
from app.web.config import WebConfig
from app.web.dependencies import require_guild_access, require_login
from app.web.sessions import SESSION_COOKIE_NAME, LoadedSession

GUILD_A = 111


def _config() -> WebConfig:
    return WebConfig(
        discord_client_id=123,
        discord_client_secret="secret",
        discord_bot_token="bot-token",
        public_base_url="https://example.com",
        internal_api_token="internal-secret",
        guild_permissions_cache_ttl_seconds=3600,
    )


def _app(http_client: httpx.AsyncClient) -> FastAPI:
    app = FastAPI()
    app.state.web_config = _config()
    app.state.http_client = http_client

    @app.get("/protected")
    async def protected(session: LoadedSession = Depends(require_login)) -> dict:
        return {"discord_user_id": session.discord_user_id}

    @app.get("/guilds/{guild_id}/protected")
    async def guild_protected(
        guild_id: int, session: LoadedSession = Depends(require_guild_access)
    ) -> dict:
        return {"guild_id": guild_id}

    return app


async def _seed_session(
    db_session: AsyncSession, *, raw_token: str, permissions: dict[str, int]
) -> None:
    import hashlib

    now = datetime.now(UTC)
    repo = WebSessionRepository(db_session)
    record = await repo.create(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        discord_user_id=42,
        discord_username="volvo",
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=now + timedelta(days=7),
        csrf_token="csrf-token",
        expires_at=now + timedelta(days=30),
    )
    record.guild_permissions_cache = permissions
    record.guild_permissions_cached_at = now
    await db_session.flush()


def _no_discord_calls(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Discord call: {request.url}")


async def test_require_login_redirects_when_no_cookie(db_session: AsyncSession) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = _app(http)
        with TestClient(app) as client:
            response = client.get("/protected", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


async def test_require_login_succeeds_with_valid_session(db_session: AsyncSession) -> None:
    await _seed_session(db_session, raw_token="good-token", permissions={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = _app(http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"discord_user_id": 42}


async def test_require_guild_access_404s_when_bot_absent(db_session: AsyncSession) -> None:
    await _seed_session(db_session, raw_token="good-token", permissions={str(GUILD_A): 0x20})
    # Deliberately not marking the bot present in GUILD_A.

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = _app(http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/protected")

    assert response.status_code == 404


async def test_require_guild_access_403s_without_manage_permission(db_session: AsyncSession) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    # SEND_MESSAGES only - no MANAGE_GUILD/ADMINISTRATOR bit.
    await _seed_session(db_session, raw_token="good-token", permissions={str(GUILD_A): 0x800})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = _app(http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/protected")

    assert response.status_code == 403


async def test_require_guild_access_succeeds_with_manage_permission(
    db_session: AsyncSession,
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await _seed_session(db_session, raw_token="good-token", permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = _app(http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/protected")

    assert response.status_code == 200
    assert response.json() == {"guild_id": GUILD_A}


async def test_require_guild_access_403s_for_guild_not_in_users_list(
    db_session: AsyncSession,
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    # Session has permissions cached for *other* guilds, not this one.
    await _seed_session(db_session, raw_token="good-token", permissions={"999": 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = _app(http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/protected")

    assert response.status_code == 403
