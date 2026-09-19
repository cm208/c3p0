from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.config_service import ConfigurationService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111

# Fixed test-guild shape: @everyone (excluded - it's the guild ID itself),
# an assignable "Member" role (below the bot's own role), and a "C3P0" role
# the bot itself holds (excluded for being `managed`, and it also defines
# the bot's top role position at 2, making "Member" at position 1
# assignable). Two ordinary text channels.
ASSIGNABLE_ROLE_ID = 500
BOT_ROLE_ID = 999
CHANNEL_A = 600
CHANNEL_B = 601


def _discord_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/roles"):
        return httpx.Response(
            200,
            json=[
                {"id": str(GUILD_A), "name": "@everyone", "position": 0, "managed": False},
                {"id": str(ASSIGNABLE_ROLE_ID), "name": "Member", "position": 1, "managed": False},
                {"id": str(BOT_ROLE_ID), "name": "C3P0", "position": 2, "managed": True},
            ],
        )
    if path.endswith("/channels"):
        return httpx.Response(
            200,
            json=[
                {"id": str(CHANNEL_A), "name": "general", "type": 0},
                {"id": str(CHANNEL_B), "name": "mod-log", "type": 0},
            ],
        )
    if "/members/" in path:
        return httpx.Response(200, json={"roles": [str(BOT_ROLE_ID)]})
    raise AssertionError(f"unexpected Discord call: {request.url}")


def _discord_unavailable(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "internal server error"})


async def test_get_general_renders_current_prefix(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await ConfigurationService().set_prefix(GUILD_A, "?")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/general")

    assert response.status_code == 200
    assert 'value="?"' in response.text


async def test_get_general_lists_assignable_roles_and_channels(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/general")

    assert response.status_code == 200
    assert ">Member<" in response.text
    assert "#general" in response.text
    assert "#mod-log" in response.text
    assert f'value="{BOT_ROLE_ID}"' not in response.text
    assert f'value="{GUILD_A}"' not in response.text


async def test_get_general_degrades_gracefully_when_discord_unavailable(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_unavailable)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/general")

    assert response.status_code == 200


async def test_get_general_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/general")

    assert response.status_code == 403


async def test_post_general_updates_prefix_via_real_service(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "$", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == f"/guilds/{GUILD_A}/general"

    config = await ConfigurationService().get_config(GUILD_A)
    assert config.prefix == "$"


async def test_post_general_sets_default_role_and_log_channel(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={
                    "prefix": "!",
                    "default_role_id": str(ASSIGNABLE_ROLE_ID),
                    "log_channel_id": str(CHANNEL_A),
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await ConfigurationService().get_config(GUILD_A)
    assert config.default_role_id == ASSIGNABLE_ROLE_ID
    assert config.log_channel_id == CHANNEL_A
    # Moderation log channel isn't touched by this page at all.
    assert config.moderation_log_channel_id is None


async def test_post_general_clears_fields_when_submitted_empty(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await ConfigurationService().set_default_role(GUILD_A, ASSIGNABLE_ROLE_ID)
    await ConfigurationService().set_log_channel(GUILD_A, CHANNEL_A)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "!", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await ConfigurationService().get_config(GUILD_A)
    assert config.default_role_id is None
    assert config.log_channel_id is None


async def test_post_general_rejects_role_above_bot_hierarchy(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "!", "default_role_id": str(BOT_ROLE_ID), "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "available anymore" in response.text
    config = await ConfigurationService().get_config(GUILD_A)
    assert config.default_role_id is None


async def test_post_general_rejects_unknown_channel_id(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "!", "log_channel_id": "999999999", "csrf_token": csrf_token},
            )

    assert response.status_code == 400


async def test_post_general_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "$", "csrf_token": "wrong-token"},
                follow_redirects=False,
            )

    assert response.status_code == 403
    config = await ConfigurationService().get_config(GUILD_A)
    assert config.prefix == "!"


async def test_post_general_rejects_invalid_prefix_without_500(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "way too long a prefix", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "characters or fewer" in response.text


async def test_post_general_404s_when_bot_absent(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/general",
                data={"prefix": "$", "csrf_token": csrf_token},
            )

    assert response.status_code == 404
