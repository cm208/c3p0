from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.role_binding import RoleBindingType
from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.role_binding_service import RoleBindingService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111

# @everyone excluded, two assignable roles below the bot's own managed role.
ROLE_A = 500
ROLE_B = 501
BOT_ROLE_ID = 999
CHANNEL_A = 600
NEW_MESSAGE_ID = 700001
EXISTING_MESSAGE_ID = 800001


def _make_discord_handler(*, fail: set[str] | None = None):
    """Records every outgoing request and fakes just enough of Discord's REST
    surface for the roles page: guild roles/channels/member (for
    load_guild_discord_state) plus message post/reaction/component-edit.

    `fail` names REST operations ("send_message", "add_reaction",
    "edit_components") that should return an error response instead of
    succeeding, to exercise this page's failure-handling paths.
    """
    calls: list[httpx.Request] = []
    fail = fail or set()
    next_id = {"value": NEW_MESSAGE_ID}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        method = request.method

        if path.endswith("/roles"):
            return httpx.Response(
                200,
                json=[
                    {"id": str(GUILD_A), "name": "@everyone", "position": 0, "managed": False},
                    {"id": str(ROLE_A), "name": "Member", "position": 1, "managed": False},
                    {"id": str(ROLE_B), "name": "VIP", "position": 1, "managed": False},
                    {"id": str(BOT_ROLE_ID), "name": "C3P0", "position": 2, "managed": True},
                ],
            )
        if path.endswith("/channels"):
            return httpx.Response(200, json=[{"id": str(CHANNEL_A), "name": "general", "type": 0}])
        if "/members/" in path:
            return httpx.Response(200, json={"roles": [str(BOT_ROLE_ID)]})

        if method == "POST" and path.endswith("/messages"):
            if "send_message" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            mid = next_id["value"]
            next_id["value"] += 1
            return httpx.Response(200, json={"id": str(mid)})

        if method == "PUT" and "/reactions/" in path and path.endswith("/@me"):
            if "add_reaction" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            return httpx.Response(204)

        if method == "DELETE" and "/reactions/" in path:
            return httpx.Response(204)

        if method == "PATCH" and "/messages/" in path:
            if "edit_components" in fail:
                return httpx.Response(400, json={"error": "bad request"})
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})

        raise AssertionError(f"unexpected Discord call: {method} {request.url}")

    return handler, calls


async def _seed(db_session: AsyncSession, seed_session: SeedSession) -> str:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    return await seed_session(db_session, permissions={str(GUILD_A): 0x20})


def _bodies_for(calls: list[httpx.Request], *, method: str, path_suffix: str) -> list[dict]:
    return [
        json.loads(c.content)
        for c in calls
        if c.method == method and c.url.path.endswith(path_suffix)
    ]


async def test_get_roles_shows_empty_state(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/roles")

    assert response.status_code == 200
    assert "No self-assignable roles configured yet" in response.text


async def test_get_roles_groups_bindings_and_resolves_names(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    service = RoleBindingService()
    await service.create_reaction_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, emoji="🎮", role_id=ROLE_A
    )
    await service.create_reaction_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, emoji="🎯", role_id=999999
    )
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/roles")

    assert response.status_code == 200
    assert "#general" in response.text
    assert "Member" in response.text
    assert "role 999999 (deleted)" in response.text


async def test_get_new_role_form_rejects_unknown_type(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/roles/new?type=nonsense")

    assert response.status_code == 400


async def test_get_new_role_form_renders_type_specific_fields(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            reaction_page = client.get(f"/guilds/{GUILD_A}/roles/new?type=reaction")
            select_page = client.get(f"/guilds/{GUILD_A}/roles/new?type=select")

    assert 'name="toggle"' in reaction_page.text
    assert 'name="placeholder"' not in reaction_page.text
    assert 'name="placeholder"' in select_page.text
    assert 'name="emoji"' not in select_page.text


async def test_post_create_reaction_role_full_sequence(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "reaction",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "message": "React below!",
                    "emoji": "🎮",
                    "toggle": "on",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert response.headers["location"] == f"/guilds/{GUILD_A}/roles"

    send_bodies = _bodies_for(calls, method="POST", path_suffix="/messages")
    assert send_bodies == [{"content": "React below!"}]
    react_calls = [c for c in calls if c.method == "PUT" and "/reactions/" in c.url.path]
    assert len(react_calls) == 1
    assert "%F0%9F%8E%AE" in str(react_calls[0].url)  # percent-encoded 🎮

    bindings = await RoleBindingService().list_bindings(GUILD_A, RoleBindingType.REACTION)
    assert len(bindings) == 1
    assert bindings[0].source_message_id == NEW_MESSAGE_ID
    assert bindings[0].emoji == "🎮"
    assert bindings[0].role_id == ROLE_A
    assert bindings[0].toggle is True


async def test_post_create_button_role_sends_message_with_components(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "button",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "message": "Click below!",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    send_bodies = _bodies_for(calls, method="POST", path_suffix="/messages")
    assert len(send_bodies) == 1
    body = send_bodies[0]
    assert body["content"] == "Click below!"
    assert body["components"] == [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "disabled": False,
                    "label": "Member",
                    "custom_id": body["components"][0]["components"][0]["custom_id"],
                }
            ],
        }
    ]
    assert body["components"][0]["components"][0]["custom_id"].startswith("c3p0:rolebtn:")

    bindings = await RoleBindingService().list_bindings(GUILD_A, RoleBindingType.BUTTON)
    assert len(bindings) == 1
    assert bindings[0].role_id == ROLE_A
    assert bindings[0].component_custom_id == body["components"][0]["components"][0]["custom_id"]


async def test_post_create_select_role_posts_then_edits_components(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "select",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "message": "Pick one!",
                    "placeholder": "Choose wisely",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    send_bodies = _bodies_for(calls, method="POST", path_suffix="/messages")
    assert send_bodies == [{"content": "Pick one!"}]  # plain, no components yet

    binding = (await RoleBindingService().list_bindings(GUILD_A, RoleBindingType.SELECT))[0]

    patch_bodies = [json.loads(c.content) for c in calls if c.method == "PATCH"]
    assert len(patch_bodies) == 1
    components = patch_bodies[0]["components"]
    assert components == [
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": f"c3p0:roleselect:{NEW_MESSAGE_ID}",
                    "min_values": 0,
                    "max_values": 1,
                    "disabled": False,
                    "required": False,
                    "placeholder": "Choose wisely",
                    "options": [{"label": "Member", "value": str(binding.id), "default": False}],
                }
            ],
        }
    ]


async def test_post_create_rejects_missing_emoji_for_reaction(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "reaction",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    assert "emoji is required" in response.text
    assert not [c for c in calls if c.method == "POST" and c.url.path.endswith("/messages")]


async def test_post_create_rejects_empty_emoji_for_reaction(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "reaction",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "emoji": "   ",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400


async def test_post_create_surfaces_discord_rejecting_the_reaction(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    # normalize_emoji doesn't fully validate real unicode emoji - arbitrary
    # text falls through as "assume unicode" and is only truly rejected when
    # Discord's own add_reaction call refuses it (see app/utils/emoji.py's
    # own docstring). Covers that rejection path specifically, distinct from
    # the send_message failure covered below.
    csrf_token = await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler(fail={"add_reaction"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "reaction",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "emoji": "not an emoji at all",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    assert "add that reaction" in response.text
    assert await RoleBindingService().list_bindings(GUILD_A) == []


async def test_post_create_rejects_stale_channel_and_role(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            no_channel = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={"type": "select", "role_id": str(ROLE_A), "csrf_token": csrf_token},
            )
            no_role = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={"type": "select", "channel_id": str(CHANNEL_A), "csrf_token": csrf_token},
            )
            bot_role_rejected = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "select",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(BOT_ROLE_ID),
                    "csrf_token": csrf_token,
                },
            )

    assert no_channel.status_code == 400
    assert no_role.status_code == 400
    assert bot_role_rejected.status_code == 400


async def test_post_create_surfaces_discord_failure_without_creating_binding(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler(fail={"send_message"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "button",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    # Jinja escapes the apostrophe in "Couldn't" to &#39; - check around it.
    assert "post that message" in response.text
    assert await RoleBindingService().list_bindings(GUILD_A) == []


async def test_post_create_select_reports_partial_failure_but_keeps_binding(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    # Matches roleselect_create's own "Posted, but couldn't attach the menu"
    # behavior - the message and binding both exist, only the component
    # attach step failed.
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler(fail={"edit_components"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "select",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 400
    # Jinja escapes the apostrophe in "couldn't" to &#39; - check around it.
    assert "Posted, but" in response.text
    assert "attach the menu" in response.text
    bindings = await RoleBindingService().list_bindings(GUILD_A)
    assert len(bindings) == 1


async def test_post_add_option_to_button_group_rebuilds_all_components(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    await service.create_button_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, role_id=ROLE_A,
        custom_id="c3p0:rolebtn:existing",
    )
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/options",
                data={"role_id": str(ROLE_B), "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    patch_bodies = [json.loads(c.content) for c in calls if c.method == "PATCH"]
    assert len(patch_bodies) == 1
    labels = [b["label"] for b in patch_bodies[0]["components"][0]["components"]]
    assert labels == ["Member", "VIP"]

    bindings = await service.list_for_message(GUILD_A, EXISTING_MESSAGE_ID, RoleBindingType.BUTTON)
    assert len(bindings) == 2


async def test_post_add_option_404s_for_unknown_message(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/options",
                data={"role_id": str(ROLE_A), "csrf_token": csrf_token},
            )

    assert response.status_code == 404


async def test_post_delete_option_reaction_is_db_only_plus_best_effort_clear(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    binding = await service.create_reaction_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, emoji="🎮", role_id=ROLE_A
    )
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/options/{binding.id}/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    delete_calls = [c for c in calls if c.method == "DELETE"]
    assert len(delete_calls) == 1
    assert await service.find_reaction_binding(GUILD_A, EXISTING_MESSAGE_ID, "🎮") is None


async def test_post_delete_option_tolerates_discord_failure_for_reaction(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    binding = await service.create_reaction_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, emoji="🎮", role_id=ROLE_A
    )

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(500, json={"error": "internal error"})
        raise AssertionError(f"unexpected call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/options/{binding.id}/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert await service.find_reaction_binding(GUILD_A, EXISTING_MESSAGE_ID, "🎮") is None


async def test_post_delete_option_clears_components_when_group_becomes_empty(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    binding = await service.create_button_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, role_id=ROLE_A,
        custom_id="c3p0:rolebtn:only",
    )
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/options/{binding.id}/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    patch_bodies = [json.loads(c.content) for c in calls if c.method == "PATCH"]
    assert patch_bodies == [{"components": []}]


async def test_post_toggle_flips_enabled_state(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    await service.create_reaction_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, emoji="🎮", role_id=ROLE_A
    )
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            first = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/toggle",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )
            second = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/toggle",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert first.status_code == 303
    assert second.status_code == 303
    assert calls == []  # DB-only, never touches Discord

    binding = await service.find_reaction_binding(GUILD_A, EXISTING_MESSAGE_ID, "🎮")
    assert binding is not None
    assert binding.enabled is True  # flipped off then back on


async def test_post_delete_group_reaction_never_touches_discord(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    await service.create_reaction_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, emoji="🎮", role_id=ROLE_A
    )
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert calls == []
    assert await service.list_for_message(GUILD_A, EXISTING_MESSAGE_ID) == []


async def test_post_delete_group_button_clears_components(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    service = RoleBindingService()
    await service.create_button_binding(
        GUILD_A, channel_id=CHANNEL_A, message_id=EXISTING_MESSAGE_ID, role_id=ROLE_A,
        custom_id="c3p0:rolebtn:x",
    )
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    patch_bodies = [json.loads(c.content) for c in calls if c.method == "PATCH"]
    assert patch_bodies == [{"components": []}]
    assert await service.list_for_message(GUILD_A, EXISTING_MESSAGE_ID) == []


async def test_post_delete_group_404s_for_unknown_message(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles/{EXISTING_MESSAGE_ID}/delete",
                data={"csrf_token": csrf_token},
            )

    assert response.status_code == 404


async def test_get_roles_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})
    handler, _calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/roles")

    assert response.status_code == 403


async def test_post_create_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    handler, calls = _make_discord_handler()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/roles",
                data={
                    "type": "select",
                    "channel_id": str(CHANNEL_A),
                    "role_id": str(ROLE_A),
                    "csrf_token": "wrong-token",
                },
            )

    assert response.status_code == 403
    assert await RoleBindingService().list_bindings(GUILD_A) == []


async def test_get_roles_404s_when_bot_absent(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/roles")

    assert response.status_code == 404
