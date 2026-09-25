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
    assert "AUTHENTICATE VIA DISCORD" in response.text
    assert 'href="/auth/login"' in response.text


def _user_guilds_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path.endswith("/users/@me/guilds")
    assert request.url.params.get("with_counts") == "true"
    return httpx.Response(
        200,
        json=[
            {"id": str(GUILD_A), "name": "Manageable Guild", "icon": None, "permissions": "32",
             "approximate_member_count": 1284, "approximate_presence_count": 311},
            {"id": str(GUILD_B), "name": "Not Manageable", "icon": None, "permissions": "2048",
             "approximate_member_count": 50, "approximate_presence_count": 5},
            {"id": "999", "name": "Homelab", "icon": None, "permissions": "32",
             "approximate_member_count": 18, "approximate_presence_count": 4},
        ],
    )


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

    async with httpx.AsyncClient(transport=httpx.MockTransport(_user_guilds_handler)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get("/")

    assert response.status_code == 200
    assert "Manageable Guild" in response.text
    assert "Not Manageable" not in response.text
    # Member/online counts come from Discord's with_counts figures.
    assert "1,284" in response.text
    assert "311 ONLINE" in response.text
    # A manageable server without the bot is listed with an invite link
    # that preselects it, instead of being hidden.
    assert "Homelab" in response.text
    assert "NOT INSTALLED" in response.text
    assert "guild_id=999" in response.text
    assert "disable_guild_select=true" in response.text


async def test_index_falls_back_to_bot_guild_names_when_discord_fails(db_session: AsyncSession) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Manageable Guild")
    await _seed_session(db_session, permissions={str(GUILD_A): 0x20, "999": 0x20})

    def _discord_down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_down)) as http:
        app = create_app(_config(), http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get("/")

    assert response.status_code == 200
    assert "Manageable Guild" in response.text
    # Without Discord, the bot-less server's name is unknown - it's omitted
    # rather than listed as a bare id.
    assert "NOT INSTALLED" not in response.text
    assert "guild_id=999" not in response.text


def test_boot_log_cogs_match_the_bots_extensions() -> None:
    from app.bot import INITIAL_EXTENSIONS
    from app.web.routers.dashboard import BOOT_COGS

    assert tuple(f"app.cogs.{cog}" for cog in BOOT_COGS) == INITIAL_EXTENSIONS
