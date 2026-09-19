from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.builtin_templates import BUILTIN_TEMPLATES, TemplateChannelDef, TemplateDefinition
from app.services.template_service import TemplateService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111
_MANAGE_GUILD = 0x20


def _role_payload(role_id: int, *, name: str | None = None, managed: bool = False, permissions: int = 0) -> dict:
    return {
        "id": str(role_id), "name": name or f"role-{role_id}", "position": 1, "managed": managed,
        "color": 0, "hoist": False, "mentionable": False, "permissions": str(permissions),
    }


def _channel_payload(channel_id: int, *, name: str | None = None, type: int = 0) -> dict:
    return {
        "id": str(channel_id), "name": name or f"channel-{channel_id}", "type": type, "position": 1,
        "parent_id": None, "topic": None, "nsfw": False, "rate_limit_per_user": 0,
        "bitrate": None, "user_limit": None, "permission_overwrites": [],
    }


def _make_discord_handler(*, extra_roles: list[dict] | None = None, extra_channels: list[dict] | None = None, fail: set[str] | None = None):
    calls: list[httpx.Request] = []
    fail = fail or set()
    next_id = {"role": 9000, "channel": 9500}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        method = request.method

        if method == "GET" and path.endswith("/roles"):
            return httpx.Response(200, json=[_role_payload(GUILD_A, name="@everyone"), *(extra_roles or [])])
        if method == "GET" and path.endswith("/channels"):
            return httpx.Response(200, json=extra_channels or [])

        if method == "POST" and path.endswith("/roles"):
            if "role" in fail:
                return httpx.Response(400, json={"error": "bad"})
            body = json.loads(request.content)
            rid = next_id["role"]
            next_id["role"] += 1
            return httpx.Response(200, json=_role_payload(rid, name=body["name"], permissions=int(body["permissions"])))

        if method == "POST" and path.endswith("/channels"):
            if "channel" in fail:
                return httpx.Response(400, json={"error": "bad"})
            body = json.loads(request.content)
            cid = next_id["channel"]
            next_id["channel"] += 1
            return httpx.Response(201, json=_channel_payload(cid, name=body["name"], type=body["type"]))

        if method == "DELETE" and "/channels/" in path and "/permissions/" not in path:
            channel_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": channel_id})

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


async def test_get_templates_lists_builtins(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/templates")

    assert response.status_code == 200
    for template in BUILTIN_TEMPLATES:
        assert template.name in response.text


async def test_save_current_layout_as_template_round_trips(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler(
        extra_roles=[_role_payload(500, name="Staff", permissions=0x2)],
        extra_channels=[_channel_payload(700, name="general")],
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates",
                data={"csrf_token": "the-csrf-token", "name": "My Layout", "description": "test"},
                follow_redirects=False,
            )
            assert response.status_code == 303

            list_response = client.get(f"/guilds/{GUILD_A}/server-management/templates")

    assert "My Layout" in list_response.text


async def test_save_current_layout_rejects_empty_name(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates",
                data={"csrf_token": "the-csrf-token", "name": "   "},
            )

    assert response.status_code == 400


async def test_apply_builtin_template_creates_items(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD | 0x2 | 0x4 | 0x2000 | 0x8000000 | 0x400)
    handler, calls = _make_discord_handler()
    slug = BUILTIN_TEMPLATES[0].slug

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates/builtin/{slug}/apply",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 200
    assert "Created" in response.text
    role_creates = [c for c in calls if c.method == "POST" and c.url.path.endswith("/roles")]
    channel_creates = [c for c in calls if c.method == "POST" and c.url.path.endswith("/channels")]
    assert len(role_creates) == len(BUILTIN_TEMPLATES[0].definition.roles)
    assert len(channel_creates) == len(BUILTIN_TEMPLATES[0].definition.channels)


async def test_apply_builtin_template_404_for_unknown_slug(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates/builtin/does-not-exist/apply",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 404


async def test_apply_builtin_template_skips_existing_role_by_name(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD | 0x2 | 0x4 | 0x2000 | 0x8000000)
    slug = BUILTIN_TEMPLATES[0].slug
    conflicting_role_name = BUILTIN_TEMPLATES[0].definition.roles[0].name
    handler, calls = _make_discord_handler(extra_roles=[_role_payload(600, name=conflicting_role_name)])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates/builtin/{slug}/apply",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 200
    assert "Already Exists" in response.text
    assert not any(c.method == "POST" and c.url.path.endswith("/roles") for c in calls)


async def test_apply_saved_template_reports_partial_failure(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    saved = await TemplateService().save_as_template(
        GUILD_A,
        name="Saved One",
        description=None,
        definition=TemplateDefinition(channels=(TemplateChannelDef(name="general", type=0),)),
        created_by=42,
    )

    # The guild currently has no "general" channel, so applying tries to
    # create it - inject a Discord failure on channel creation to exercise
    # the partial-completion result page.
    handler, _calls = _make_discord_handler(fail={"channel"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates/{saved.id}/apply",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 200
    assert "Failed" in response.text


async def test_delete_template_404_for_missing(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates/999999/delete",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 404


async def test_templates_list_warns_about_the_channel_wipe(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/templates")

    assert "PERMANENTLY DELETE every channel" in response.text


async def test_preview_builtin_template_shows_its_roles_and_channels(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()
    template = BUILTIN_TEMPLATES[0]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/templates/builtin/{template.slug}/preview")

    assert response.status_code == 200
    assert template.definition.roles[0].name in response.text
    assert template.definition.channels[0].name in response.text
    assert "delete" in response.text.lower()


async def test_preview_builtin_template_404_for_unknown_slug(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(f"/guilds/{GUILD_A}/server-management/templates/builtin/does-not-exist/preview")

    assert response.status_code == 404


async def test_preview_saved_template_404_for_other_guilds_template(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    other_guild_template = await TemplateService().save_as_template(
        999, name="Not Yours", description=None, definition=TemplateDefinition(), created_by=1,
    )
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.get(
                f"/guilds/{GUILD_A}/server-management/templates/{other_guild_template.id}/preview"
            )

    assert response.status_code == 404


async def test_apply_builtin_template_deletes_existing_channels_first(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=_MANAGE_GUILD | 0x2 | 0x4 | 0x2000 | 0x8000000 | 0x400)
    handler, calls = _make_discord_handler(
        extra_channels=[_channel_payload(700, name="pre-existing-channel")],
    )
    slug = BUILTIN_TEMPLATES[0].slug

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with _client(web_config, http) as client:
            response = client.post(
                f"/guilds/{GUILD_A}/server-management/templates/builtin/{slug}/apply",
                data={"csrf_token": "the-csrf-token"},
            )

    assert response.status_code == 200
    assert "Deleted" in response.text
    channel_deletes = [c for c in calls if c.method == "DELETE" and c.url.path.endswith("/channels/700")]
    assert len(channel_deletes) == 1
