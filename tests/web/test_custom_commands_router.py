from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.custom_command_service import CustomCommandService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111

MEMBER_ROLE_ID = 500
BOT_ROLE_ID = 999


def _discord_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/roles"):
        return httpx.Response(
            200,
            json=[
                {"id": str(GUILD_A), "name": "@everyone", "position": 0, "managed": False},
                {"id": str(MEMBER_ROLE_ID), "name": "Member", "position": 1, "managed": False},
                {"id": str(BOT_ROLE_ID), "name": "C3P0", "position": 2, "managed": True},
            ],
        )
    if path.endswith("/channels"):
        return httpx.Response(200, json=[])
    if "/members/" in path:
        return httpx.Response(200, json={"roles": [str(BOT_ROLE_ID)]})
    raise AssertionError(f"unexpected Discord call: {request.url}")


async def _seed(db_session: AsyncSession, seed_session: SeedSession) -> str:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    return await seed_session(db_session, permissions={str(GUILD_A): 0x20})


async def test_get_list_shows_empty_state(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/custom-commands")

    assert response.status_code == 200
    assert "No custom commands yet" in response.text


async def test_get_list_shows_existing_commands(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=42
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/custom-commands")

    assert response.status_code == 200
    assert "!rules" in response.text
    assert "Rules" in response.text


async def test_get_list_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/custom-commands")

    assert response.status_code == 403


async def test_post_create_persists_and_redirects(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands",
                data={
                    "name": "Rules",
                    "trigger": "!rules",
                    "response": "Be nice, {user}.",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == f"/guilds/{GUILD_A}/custom-commands"

    command = await CustomCommandService().get_by_trigger(GUILD_A, "!rules")
    assert command is not None
    assert command.created_by == 42  # seed_session's default discord_user_id


async def test_post_create_rejects_duplicate_trigger(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands",
                data={
                    "name": "Rules 2",
                    "trigger": "!rules",
                    "response": "Other.",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    assert "already exists" in response.text


async def test_get_edit_renders_command(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/custom-commands/!rules")

    assert response.status_code == 200
    assert "Be nice." in response.text
    assert ">Member<" in response.text


async def test_get_edit_404s_for_unknown_trigger(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/custom-commands/!missing")

    assert response.status_code == 404


async def test_post_edit_updates_response_and_toggles(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules",
                data={
                    "response": "New {user}.",
                    "embed_enabled": "on",
                    "usage_logging_enabled": "on",
                    "restriction_type": "public",
                    "cooldown_type": "none",
                    "cooldown_seconds": "0",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == f"/guilds/{GUILD_A}/custom-commands/%21rules"

    command = await CustomCommandService().get_by_trigger(GUILD_A, "!rules")
    assert command is not None
    assert command.response == "New {user}."
    assert command.embed_enabled is True
    assert command.usage_logging_enabled is True
    assert command.enabled is False  # checkbox omitted from this submit


async def test_post_edit_role_restriction_requires_a_role(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules",
                data={
                    "response": "Old.",
                    "restriction_type": "role",
                    "cooldown_type": "none",
                    "cooldown_seconds": "0",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    assert "Select a role" in response.text


async def test_post_edit_persists_role_restriction_including_managed_role(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    # all_roles, not assignable_roles - a plain membership check, so the
    # bot's own managed role is a legitimate restriction target.
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules",
                data={
                    "response": "Old.",
                    "restriction_type": "role",
                    "restricted_role_id": str(BOT_ROLE_ID),
                    "cooldown_type": "none",
                    "cooldown_seconds": "0",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    command = await CustomCommandService().get_by_trigger(GUILD_A, "!rules")
    assert command is not None
    assert command.restriction_type == "role"
    assert command.restricted_role_id == BOT_ROLE_ID


async def test_post_edit_rejects_unknown_permission_name(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules",
                data={
                    "response": "Old.",
                    "restriction_type": "permission",
                    "restricted_permission": "not_a_real_permission",
                    "cooldown_type": "none",
                    "cooldown_seconds": "0",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    # Jinja escapes the apostrophe in "isn't" to &#39; - check around it instead.
    assert "not_a_real_permission" in response.text
    assert "real Discord permission" in response.text


async def test_post_edit_persists_valid_permission_restriction(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules",
                data={
                    "response": "Old.",
                    "restriction_type": "permission",
                    "restricted_permission": "manage_messages",
                    "cooldown_type": "none",
                    "cooldown_seconds": "0",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    command = await CustomCommandService().get_by_trigger(GUILD_A, "!rules")
    assert command is not None
    assert command.restriction_type == "permission"
    assert command.restricted_permission == "manage_messages"


async def test_post_edit_rejects_negative_cooldown(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules",
                data={
                    "response": "Old.",
                    "restriction_type": "public",
                    "cooldown_type": "user",
                    "cooldown_seconds": "-5",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    assert "negative" in response.text


async def test_post_edit_404s_for_unknown_trigger(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!missing",
                data={
                    "response": "x",
                    "restriction_type": "public",
                    "cooldown_type": "none",
                    "cooldown_seconds": "0",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 404


async def test_post_delete_removes_command(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await CustomCommandService().create(
        GUILD_A, name="Rules", trigger="!rules", response="Old.", created_by=1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!rules/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert await CustomCommandService().get_by_trigger(GUILD_A, "!rules") is None


async def test_post_delete_404s_for_unknown_trigger(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands/!missing/delete",
                data={"csrf_token": csrf_token},
            )

    assert response.status_code == 404


async def test_post_create_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/custom-commands",
                data={
                    "name": "Rules",
                    "trigger": "!rules",
                    "response": "Be nice.",
                    "csrf_token": "wrong-token",
                },
            )

    assert response.status_code == 403
    assert await CustomCommandService().get_by_trigger(GUILD_A, "!rules") is None


async def test_get_list_404s_when_bot_absent(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/custom-commands")

    assert response.status_code == 404
