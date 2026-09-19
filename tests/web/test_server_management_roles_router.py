from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.audit_log_service import AuditLogService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111
BOT_USER_ID = 123  # matches web_config fixture's discord_client_id

ROLE_EDITABLE = 500
ROLE_WITH_KICK = 501  # already has KICK_MEMBERS, below the bot's top role
ROLE_MANAGED = 502
ROLE_ABOVE_BOT = 503
BOT_ROLE_ID = 900

_KICK_MEMBERS = 0x2
_MANAGE_GUILD = 0x20
_BAN_MEMBERS = 0x4


def _role_payload(role_id: int, *, position: int, managed: bool = False, permissions: int = 0, name: str | None = None) -> dict:
    return {
        "id": str(role_id),
        "name": name or f"role-{role_id}",
        "position": position,
        "managed": managed,
        "color": 0,
        "hoist": False,
        "mentionable": False,
        "permissions": str(permissions),
    }


def _make_discord_handler(*, fail: set[str] | None = None):
    calls: list[httpx.Request] = []
    fail = fail or set()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        method = request.method

        if method == "GET" and path.endswith("/roles"):
            return httpx.Response(
                200,
                json=[
                    _role_payload(GUILD_A, position=0, name="@everyone"),
                    _role_payload(ROLE_EDITABLE, position=1, name="Member"),
                    _role_payload(ROLE_WITH_KICK, position=2, name="Helper", permissions=_KICK_MEMBERS),
                    _role_payload(ROLE_MANAGED, position=3, name="Integration", managed=True),
                    _role_payload(BOT_ROLE_ID, position=4, name="C3P0", managed=True),
                    _role_payload(ROLE_ABOVE_BOT, position=5, name="Owner-only"),
                ],
            )
        if method == "GET" and f"/members/{BOT_USER_ID}" in path:
            return httpx.Response(200, json={"roles": [str(BOT_ROLE_ID)]})

        if method == "POST" and path.endswith("/roles"):
            if "create" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json=_role_payload(999, position=1, name=body["name"], permissions=int(body["permissions"])),
            )

        if method == "PATCH" and path.endswith("/roles"):
            if "position" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            return httpx.Response(200, json=[])

        if method == "PATCH" and "/roles/" in path:
            if "edit" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            role_id = int(path.rsplit("/", 1)[-1])
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json=_role_payload(role_id, position=1, name=body["name"], permissions=int(body["permissions"])),
            )

        if method == "DELETE" and "/roles/" in path:
            if "delete" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            return httpx.Response(204)

        raise AssertionError(f"unexpected Discord call: {method} {request.url}")

    return handler, calls


async def _seed(db_session: AsyncSession, seed_session: SeedSession, *, permissions: int = _MANAGE_GUILD) -> str:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    return await seed_session(db_session, permissions={str(GUILD_A): permissions})


def _client(web_config: WebConfig, http: httpx.AsyncClient) -> TestClient:
    app = create_app(web_config, http_client=http)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "good-token")
    return client


async def test_get_roles_excludes_everyone_and_shows_all_others(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/roles")

    assert response.status_code == 200
    assert "@everyone" not in response.text
    assert "Member" in response.text
    assert "Integration" in response.text
    assert "Owner-only" in response.text


async def test_get_roles_requires_manage_access(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=0)  # no manage access
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/roles")

    assert response.status_code == 403


async def test_create_role_persists_and_records_audit(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD | _KICK_MEMBERS)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles",
                data={
                    "csrf_token": "the-csrf-token",
                    "name": "Staff",
                    "color": "0",
                    "hoist": "on",
                    "permissions": ["kick_members"],
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    create_calls = [c for c in calls if c.method == "POST" and c.url.path.endswith("/roles")]
    assert len(create_calls) == 1
    body = json.loads(create_calls[0].content)
    assert body["name"] == "Staff"
    assert int(body["permissions"]) == _KICK_MEMBERS

    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].target_name == "Staff"


async def test_create_role_rejects_permission_operator_lacks(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD)  # no kick_members
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles",
                data={
                    "csrf_token": "the-csrf-token",
                    "name": "Staff",
                    "color": "0",
                    "permissions": ["kick_members"],
                },
            )

    assert response.status_code == 400
    assert "grant a permission" in response.text
    assert not any(c.method == "POST" and c.url.path.endswith("/roles") for c in calls)
    assert await AuditLogService().list_for_guild(GUILD_A) == []


async def test_create_role_requires_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles",
                data={"csrf_token": "wrong-token", "name": "Staff"},
            )

    assert response.status_code == 403


async def test_edit_role_form_404_for_managed_role(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/roles/{ROLE_MANAGED}/edit")

    assert response.status_code == 404


async def test_edit_role_form_404_for_role_above_bot(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/roles/{ROLE_ABOVE_BOT}/edit")

    assert response.status_code == 404


async def test_edit_role_preserves_existing_permission_operator_does_not_hold(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    # ROLE_WITH_KICK already has KICK_MEMBERS. The operator only holds
    # MANAGE_GUILD - an edit that leaves the kick_members checkbox checked
    # (unchanged) must NOT be rejected, since nothing new is being granted.
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/{ROLE_WITH_KICK}",
                data={
                    "csrf_token": "the-csrf-token",
                    "name": "Helper Renamed",
                    "color": "0",
                    "permissions": ["kick_members"],
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    edit_calls = [c for c in calls if c.method == "PATCH" and c.url.path.endswith(f"/roles/{ROLE_WITH_KICK}")]
    assert len(edit_calls) == 1
    body = json.loads(edit_calls[0].content)
    assert body["name"] == "Helper Renamed"
    assert int(body["permissions"]) == _KICK_MEMBERS


async def test_edit_role_rejects_newly_granted_permission_operator_lacks(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    # Same role, but this time trying to ALSO add ban_members, which the
    # operator doesn't hold - that bit is newly granted, so it must be
    # rejected even though kick_members (already on the role) is fine.
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/{ROLE_WITH_KICK}",
                data={
                    "csrf_token": "the-csrf-token",
                    "name": "Helper",
                    "color": "0",
                    "permissions": ["kick_members", "ban_members"],
                },
            )

    assert response.status_code == 400
    assert not any(c.method == "PATCH" and c.url.path.endswith(f"/roles/{ROLE_WITH_KICK}") for c in calls)


async def test_delete_role_records_audit(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/{ROLE_EDITABLE}/delete",
                data={"csrf_token": "the-csrf-token"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert any(c.method == "DELETE" and c.url.path.endswith(f"/roles/{ROLE_EDITABLE}") for c in calls)
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].action.value == "role_delete"


async def test_delete_role_404_for_managed_role(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/{ROLE_MANAGED}/delete",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 404


async def test_reorder_roles_permutes_only_editable_positions(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    # Editable roles are ROLE_EDITABLE (position 1) and ROLE_WITH_KICK
    # (position 2) - the submitted top-to-bottom order [ROLE_WITH_KICK,
    # ROLE_EDITABLE] should get those two position values reassigned in
    # that order (highest first), never a client-supplied number, and
    # never touch ROLE_MANAGED/BOT_ROLE_ID/ROLE_ABOVE_BOT's positions.
    await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/reorder",
                data={"csrf_token": "the-csrf-token", "order": json.dumps([ROLE_WITH_KICK, ROLE_EDITABLE])},
                follow_redirects=False,
            )

    assert response.status_code == 303
    position_calls = [c for c in calls if c.method == "PATCH" and c.url.path.endswith("/roles")]
    assert len(position_calls) == 1
    assert json.loads(position_calls[0].content) == [
        {"id": str(ROLE_WITH_KICK), "position": 2},
        {"id": str(ROLE_EDITABLE), "position": 1},
    ]


async def test_reorder_roles_rejects_order_not_matching_editable_set(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/reorder",
                data={"csrf_token": "the-csrf-token", "order": json.dumps([ROLE_EDITABLE, ROLE_MANAGED])},
            )

    assert response.status_code == 409
    assert not any(c.method == "PATCH" and c.url.path.endswith("/roles") for c in calls)


async def test_reorder_roles_requires_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/roles/reorder",
                data={"csrf_token": "wrong-token", "order": json.dumps([ROLE_EDITABLE, ROLE_WITH_KICK])},
            )

    assert response.status_code == 403
    assert not any(c.method == "PATCH" and c.url.path.endswith("/roles") for c in calls)
