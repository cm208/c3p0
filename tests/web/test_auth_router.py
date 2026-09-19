from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.web_session_repository import WebSessionRepository
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME, _hash_token


def _config() -> WebConfig:
    return WebConfig(
        discord_client_id=123,
        discord_client_secret="secret",
        discord_bot_token="bot-token",
        public_base_url="https://example.com",
        internal_api_token="internal-secret",
        cookie_secure=False,  # TestClient doesn't use https
    )


async def test_login_sets_state_cookie_and_redirects(db_session: AsyncSession) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://discord.com/oauth2/authorize")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["123"]
    assert query["redirect_uri"] == ["https://example.com/auth/callback"]
    assert "oauth_state" in response.cookies
    assert query["state"] == [response.cookies["oauth_state"]]


async def test_callback_rejects_mismatched_state(db_session: AsyncSession) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set("oauth_state", "expected-state")
            response = client.get(
                "/auth/callback",
                params={"code": "abc", "state": "wrong-state"},
                follow_redirects=False,
            )

    assert response.status_code == 400


async def test_callback_rejects_missing_state_cookie(db_session: AsyncSession) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            response = client.get(
                "/auth/callback", params={"code": "abc", "state": "some-state"}
            )

    assert response.status_code == 400


async def test_callback_happy_path_creates_session(db_session: AsyncSession) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 604800}
            )
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "42", "username": "volvo", "avatar": None})
        raise AssertionError(f"unexpected call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set("oauth_state", "matching-state")
            response = client.get(
                "/auth/callback",
                params={"code": "abc", "state": "matching-state"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE_NAME in response.cookies

    raw_token = response.cookies[SESSION_COOKIE_NAME]
    record = await WebSessionRepository(db_session).get_by_token_hash(_hash_token(raw_token))
    assert record is not None
    assert record.discord_user_id == 42
    assert record.discord_username == "volvo"


async def test_callback_returns_502_on_discord_error(db_session: AsyncSession) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set("oauth_state", "matching-state")
            response = client.get(
                "/auth/callback",
                params={"code": "abc", "state": "matching-state"},
                follow_redirects=False,
            )

    assert response.status_code == 502
    assert SESSION_COOKIE_NAME not in response.cookies


async def test_logout_deletes_session_and_cookie(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    await WebSessionRepository(db_session).create(
        token_hash=_hash_token("raw-token"),
        discord_user_id=42,
        discord_username="volvo",
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=now + timedelta(days=7),
        csrf_token="csrf",
        expires_at=now + timedelta(days=30),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "raw-token")
            response = client.post("/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    assert await WebSessionRepository(db_session).get_by_token_hash(_hash_token("raw-token")) is None
