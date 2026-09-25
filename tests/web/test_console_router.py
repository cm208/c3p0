from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.event_log_service import EventLogService, EventTag
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111


def _no_discord(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Discord call: {request.url}")


def _status_handler(calls: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/status"
        assert request.headers["Authorization"] == "Bearer internal-secret"
        return httpx.Response(
            200,
            json={
                "version": "1.4.0", "ready": True, "latency_ms": 41, "shard_id": 0, "shard_count": 1,
                "guild_count": 3, "started_at": "2026-09-10T08:00:00+00:00", "uptime_seconds": 120,
            },
        )

    return handler


async def test_api_status_proxies_and_caches_the_bot_status(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await seed_session(db_session)
    calls: list[httpx.Request] = []

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_no_discord)) as http,
        httpx.AsyncClient(transport=httpx.MockTransport(_status_handler(calls))) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            first = client.get("/api/status").json()
            second = client.get("/api/status").json()

    assert first["reachable"] is True
    assert first["latency_ms"] == 41
    assert first == second
    assert len(calls) == 1  # cached across rapid polls from open tabs


async def test_api_status_reports_unreachable_bot(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await seed_session(db_session)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("restarting")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(_no_discord)) as http,
        httpx.AsyncClient(transport=httpx.MockTransport(down)) as bot_http,
    ):
        app = create_app(web_config, http_client=http, bot_http_client=bot_http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {"reachable": False}


async def test_api_status_requires_login(db_session: AsyncSession, web_config: WebConfig) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            response = client.get("/api/status", follow_redirects=False)

    assert response.status_code == 303


async def test_guild_events_returns_new_events_after_an_id(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    service = EventLogService()
    await service.record(GUILD_A, EventTag.JOIN, "@alice joined")
    await service.record(GUILD_A, EventTag.CMD, "!rules invoked by @alice")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            everything = client.get(f"/guilds/{GUILD_A}/events").json()
            first_id = everything[0]["id"]
            newer = client.get(f"/guilds/{GUILD_A}/events?after={first_id}").json()

    assert [(e["tag"], e["text"]) for e in everything] == [
        ("JOIN", "@alice joined"),
        ("CMD", "!rules invoked by @alice"),
    ]
    assert [e["text"] for e in newer] == ["!rules invoked by @alice"]
    assert "ts" in everything[0]


async def test_guild_events_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_discord)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/events")

    assert response.status_code == 403
