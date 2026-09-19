from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.db.repositories.web_session_repository import WebSessionRepository
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME

GUILD_A = 111
GUILD_B = 222


def _config() -> WebConfig:
    return WebConfig(
        discord_client_id=123,
        discord_client_secret="secret",
        discord_bot_token="bot-token",
        public_base_url="https://example.com",
        internal_api_token="internal-secret",
        cookie_secure=False,
    )


def _no_discord_calls(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Discord call: {request.url}")


async def _seed_session(db_session: AsyncSession, *, permissions: dict[str, int]) -> None:
    now = datetime.now(UTC)
    record = await WebSessionRepository(db_session).create(
        token_hash=hashlib.sha256(b"good-token").hexdigest(),
        discord_user_id=42,
        discord_username="volvo",
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=now + timedelta(days=7),
        csrf_token="csrf",
        expires_at=now + timedelta(days=30),
    )
    record.guild_permissions_cache = permissions
    record.guild_permissions_cached_at = now
    await db_session.flush()


async def test_healthz_is_unauthenticated(db_session: AsyncSession) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_index_shows_login_page_when_logged_out(db_session: AsyncSession) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            response = client.get("/")

    assert response.status_code == 200
    assert "Authenticate with Discord" in response.text


async def test_index_lists_only_manageable_bot_present_guilds(db_session: AsyncSession) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Manageable Guild")
    # GUILD_B: bot present but user has no manage permission there.
    await BotGuildRepository(db_session).mark_present(GUILD_B, "Not Manageable")
    await _seed_session(
        db_session,
        permissions={
            str(GUILD_A): 0x20,  # MANAGE_GUILD
            str(GUILD_B): 0x800,  # SEND_MESSAGES only
            "999": 0x20,  # manageable, but bot isn't in this one
        },
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get("/")

    assert response.status_code == 200
    assert "Manageable Guild" in response.text
    assert "Not Manageable" not in response.text
