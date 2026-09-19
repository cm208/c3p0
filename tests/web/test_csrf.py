from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.web_session_repository import WebSessionRepository
from app.web.config import WebConfig
from app.web.csrf import require_csrf
from app.web.sessions import SESSION_COOKIE_NAME, LoadedSession


def _no_discord_calls(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Discord call: {request.url}")


def _app(http_client: httpx.AsyncClient) -> FastAPI:
    app = FastAPI()
    app.state.web_config = WebConfig(
        discord_client_id=123,
        discord_client_secret="secret",
        discord_bot_token="bot-token",
        public_base_url="https://example.com",
        internal_api_token="internal-secret",
    )
    app.state.http_client = http_client

    @app.post("/mutate")
    async def mutate(session: LoadedSession = Depends(require_csrf)) -> dict:
        return {"ok": True}

    return app


async def _seed_session(db_session: AsyncSession, *, raw_token: str, csrf_token: str) -> None:
    now = datetime.now(UTC)
    record = await WebSessionRepository(db_session).create(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        discord_user_id=42,
        discord_username="volvo",
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=now + timedelta(days=7),
        csrf_token=csrf_token,
        expires_at=now + timedelta(days=30),
    )
    # Fresh (empty) permissions cache so require_login doesn't need to hit
    # Discord at all - irrelevant to what this test file is checking.
    record.guild_permissions_cache = {}
    record.guild_permissions_cached_at = now
    await db_session.flush()


async def test_require_csrf_rejects_missing_token(db_session: AsyncSession) -> None:
    await _seed_session(db_session, raw_token="good-token", csrf_token="real-csrf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        with TestClient(_app(http)) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post("/mutate", data={})

    assert response.status_code == 403


async def test_require_csrf_rejects_wrong_token(db_session: AsyncSession) -> None:
    await _seed_session(db_session, raw_token="good-token", csrf_token="real-csrf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        with TestClient(_app(http)) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post("/mutate", data={"csrf_token": "wrong"})

    assert response.status_code == 403


async def test_require_csrf_accepts_matching_token(db_session: AsyncSession) -> None:
    await _seed_session(db_session, raw_token="good-token", csrf_token="real-csrf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord_calls)) as http:
        with TestClient(_app(http)) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post("/mutate", data={"csrf_token": "real-csrf"})

    assert response.status_code == 200
