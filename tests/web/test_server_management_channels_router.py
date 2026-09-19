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
BOT_USER_ID = 123

ROLE_A = 500  # assignable, non-everyone
CATEGORY_ID = 700
CHANNEL_TEXT = 800  # under CATEGORY_ID
CHANNEL_VOICE = 801  # no category
CHANNEL_WITH_OVERWRITE = 802

_MANAGE_GUILD = 0x20
_VIEW_CHANNEL = 0x400


def _role_payload(role_id: int, *, name: str | None = None, managed: bool = False) -> dict:
    return {
        "id": str(role_id), "name": name or f"role-{role_id}", "position": 1, "managed": managed,
        "color": 0, "hoist": False, "mentionable": False, "permissions": "0",
    }


def _channel_payload(
    channel_id: int, *, type: int = 0, name: str | None = None, parent_id: int | None = None,
    overwrites: list[dict] | None = None,
) -> dict:
    return {
        "id": str(channel_id), "name": name or f"channel-{channel_id}", "type": type, "position": 1,
        "parent_id": str(parent_id) if parent_id else None, "topic": None, "nsfw": False,
        "rate_limit_per_user": 0, "bitrate": None, "user_limit": None,
        "permission_overwrites": overwrites or [],
    }


def _make_discord_handler(*, fail: set[str] | None = None):
    calls: list[httpx.Request] = []
    fail = fail or set()
    next_id = {"value": 9000}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        method = request.method

        if method == "GET" and path.endswith("/roles"):
            return httpx.Response(
                200,
                json=[
                    _role_payload(GUILD_A, name="@everyone"),
                    _role_payload(ROLE_A, name="Member"),
                ],
            )
        if method == "GET" and path.endswith("/channels"):
            return httpx.Response(
                200,
                json=[
                    _channel_payload(CATEGORY_ID, type=4, name="Category"),
                    _channel_payload(CHANNEL_TEXT, type=0, name="general", parent_id=CATEGORY_ID),
                    _channel_payload(CHANNEL_VOICE, type=2, name="Voice"),
                    _channel_payload(
                        CHANNEL_WITH_OVERWRITE, type=0, name="mod-chat",
                        overwrites=[{"id": str(GUILD_A), "type": 0, "allow": "0", "deny": str(_VIEW_CHANNEL)}],
                    ),
                ],
            )
        if method == "GET" and f"/members/{BOT_USER_ID}" in path:
            return httpx.Response(200, json={"roles": []})

        if method == "POST" and path.endswith("/channels"):
            if "create" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            body = json.loads(request.content)
            if body["name"] in fail:
                return httpx.Response(400, json={"error": "bad request"})
            channel_id = next_id["value"]
            next_id["value"] += 1
            return httpx.Response(
                201,
                json=_channel_payload(
                    channel_id, type=body["type"], name=body["name"],
                    overwrites=body.get("permission_overwrites", []),
                ),
            )

        if method == "PATCH" and "/channels/" in path and path.rsplit("/", 1)[-1].isdigit():
            if "edit" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            channel_id = int(path.rsplit("/", 1)[-1])
            body = json.loads(request.content)
            return httpx.Response(200, json=_channel_payload(channel_id, name=body["name"]))

        if method == "DELETE" and "/permissions/" in path:
            return httpx.Response(204)

        if method == "PUT" and "/permissions/" in path:
            if "overwrite" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            return httpx.Response(204)

        if method == "DELETE" and "/channels/" in path and "/permissions/" not in path:
            if "delete" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            channel_id = int(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=_channel_payload(channel_id))

        if method == "PATCH" and path.endswith(f"/guilds/{GUILD_A}/channels"):
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


def _post_batch(client: TestClient, csrf_token: str, batch: dict, **kwargs):
    return client.post(
        f"/guilds/{GUILD_A}/server-management/channels/canvas/apply",
        data={"csrf_token": csrf_token, "batch": json.dumps(batch)},
        **kwargs,
    )


# --- GET the canvas view ---


async def test_get_channel_canvas_embeds_live_state_as_json(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/channels")

    assert response.status_code == 200
    assert "general" in response.text
    assert "Voice" in response.text
    assert "Category" in response.text
    assert str(CHANNEL_WITH_OVERWRITE) in response.text


async def test_get_channel_canvas_404s_when_bot_absent(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await seed_session(db_session, permissions={str(GUILD_A): _MANAGE_GUILD})

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/channels")

    assert response.status_code == 404


# --- POST the canvas batch apply ---


async def test_apply_canvas_batch_creates_channel(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session, permissions=_MANAGE_GUILD | _VIEW_CHANNEL)
    handler, calls = _make_discord_handler()
    batch = {"channels": [{"op": "create", "temp_id": "a", "type": 0, "name": "new-channel"}]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = _post_batch(client, csrf_token, batch)

    assert response.status_code == 200
    assert "Created" in response.text
    create_calls = [c for c in calls if c.method == "POST" and c.url.path.endswith("/channels")]
    assert len(create_calls) == 1

    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].action.value == "channel_create"


async def test_apply_canvas_batch_deletes_channel(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()
    batch = {"channels": [{"op": "delete", "id": CHANNEL_VOICE}]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = _post_batch(client, csrf_token, batch)

    assert response.status_code == 200
    assert "Deleted" in response.text
    assert any(c.method == "DELETE" and c.url.path.endswith(f"/channels/{CHANNEL_VOICE}") for c in calls)
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert entries[0].action.value == "channel_delete"


async def test_apply_canvas_batch_edits_channel_name(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()
    batch = {"channels": [{"op": "edit", "id": CHANNEL_TEXT, "name": "renamed", "type": 0}]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = _post_batch(client, csrf_token, batch)

    assert response.status_code == 200
    edit_calls = [c for c in calls if c.method == "PATCH" and c.url.path.endswith(f"/channels/{CHANNEL_TEXT}")]
    assert len(edit_calls) == 1
    assert json.loads(edit_calls[0].content)["name"] == "renamed"
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert entries[0].action.value == "channel_edit"


async def test_apply_canvas_batch_reorders_channels(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()
    batch = {"positions": [{"ref": {"kind": "existing", "id": CHANNEL_TEXT}, "position": 5}]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = _post_batch(client, csrf_token, batch)

    assert response.status_code == 200
    reorder_calls = [c for c in calls if c.method == "PATCH" and c.url.path.endswith(f"/guilds/{GUILD_A}/channels")]
    assert len(reorder_calls) == 1
    assert json.loads(reorder_calls[0].content) == [{"id": str(CHANNEL_TEXT), "position": 5, "parent_id": None}]


async def test_apply_canvas_batch_rejects_malformed_json(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/channels/canvas/apply",
                data={"csrf_token": csrf_token, "batch": "not json"},
            )

    assert response.status_code == 400


async def test_apply_canvas_batch_rejects_invalid_batch_shape(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()
    batch = {"channels": [{"op": "not-a-real-op"}]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = _post_batch(client, csrf_token, batch)

    assert response.status_code == 400


async def test_apply_canvas_batch_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()
    batch = {"channels": [{"op": "delete", "id": CHANNEL_VOICE}]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = _post_batch(client, "wrong-token", batch)

    assert response.status_code == 403
    assert not any(c.method == "DELETE" for c in calls)
