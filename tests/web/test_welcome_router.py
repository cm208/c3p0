from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.config_service import ConfigurationService
from app.services.welcome_service import WelcomeService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111

# Same fixed test-guild shape as test_general_router.py: @everyone (excluded),
# an assignable "Member" role below the bot's own "C3P0" role, one text channel.
ASSIGNABLE_ROLE_ID = 500
BOT_ROLE_ID = 999
CHANNEL_A = 600


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
            200, json=[{"id": str(CHANNEL_A), "name": "welcome", "type": 0}]
        )
    if "/members/" in path:
        return httpx.Response(200, json={"roles": [str(BOT_ROLE_ID)]})
    raise AssertionError(f"unexpected Discord call: {request.url}")


async def test_get_welcome_renders_current_config(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await WelcomeService().set_message(GUILD_A, "Welcome {user_mention}!")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/welcome")

    assert response.status_code == 200
    assert "Welcome {user_mention}!" in response.text
    assert "#welcome" in response.text
    assert ">Member<" in response.text


async def test_get_welcome_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/welcome")

    assert response.status_code == 403


async def test_post_welcome_updates_master_switch_and_channel(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={
                    "enabled": "on",
                    "channel_id": str(CHANNEL_A),
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == f"/guilds/{GUILD_A}/welcome"

    config = await WelcomeService().get_config(GUILD_A)
    assert config.enabled is True
    assert config.channel_id == CHANNEL_A
    assert config.message_enabled is False


async def test_post_welcome_blank_message_template_leaves_it_unchanged(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await WelcomeService().set_message(GUILD_A, "Existing template {user}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"message_template": "   ", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await WelcomeService().get_config(GUILD_A)
    assert config.message_template == "Existing template {user}"


async def test_post_welcome_rejects_unknown_template_variable(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"message_template": "Welcome {typo}", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "{typo}" in response.text
    config = await WelcomeService().get_config(GUILD_A)
    assert config.message_template is None


async def test_post_welcome_clears_embed_field_when_submitted_empty(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await WelcomeService().set_embed_footer(GUILD_A, "Some footer")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await WelcomeService().get_config(GUILD_A)
    assert config.embed_footer is None


async def test_post_welcome_enables_role_and_sets_id_together(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={
                    "role_enabled": "on",
                    "role_id": str(ASSIGNABLE_ROLE_ID),
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await WelcomeService().get_config(GUILD_A)
    assert config.role_enabled is True
    assert config.role_id == ASSIGNABLE_ROLE_ID


async def test_post_welcome_rejects_role_enable_without_role_selected(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"role_enabled": "on", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "Select a role" in response.text
    config = await WelcomeService().get_config(GUILD_A)
    assert config.role_enabled is False


async def test_post_welcome_disable_role_keeps_role_id(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await WelcomeService().set_role(GUILD_A, ASSIGNABLE_ROLE_ID)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"role_id": str(ASSIGNABLE_ROLE_ID), "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await WelcomeService().get_config(GUILD_A)
    assert config.role_enabled is False
    assert config.role_id == ASSIGNABLE_ROLE_ID


async def test_post_welcome_rejects_join_log_without_log_channel(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"join_log_enabled": "on", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "General" in response.text
    config = await WelcomeService().get_config(GUILD_A)
    assert config.join_log_enabled is False


async def test_post_welcome_allows_join_log_once_log_channel_set(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})
    await ConfigurationService().set_log_channel(GUILD_A, CHANNEL_A)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"join_log_enabled": "on", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await WelcomeService().get_config(GUILD_A)
    assert config.join_log_enabled is True


async def test_post_welcome_rejects_channel_above_stale_id(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    csrf_token = await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"channel_id": "999999999", "csrf_token": csrf_token},
            )

    assert response.status_code == 400


async def test_post_welcome_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/welcome",
                data={"enabled": "on", "csrf_token": "wrong-token"},
                follow_redirects=False,
            )

    assert response.status_code == 403
    config = await WelcomeService().get_config(GUILD_A)
    assert config.enabled is False


async def test_post_welcome_404s_when_bot_absent(
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
                f"/guilds/{GUILD_A}/welcome",
                data={"csrf_token": csrf_token},
            )

    assert response.status_code == 404
