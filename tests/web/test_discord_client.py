from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.web.discord_client import (
    DiscordAPIError,
    DiscordPermissionOverwrite,
    DiscordRole,
    add_own_reaction,
    bot_top_role_position,
    build_authorize_url,
    bulk_edit_channel_positions,
    bulk_edit_role_positions,
    clear_reaction,
    create_guild_channel,
    create_guild_role,
    delete_channel_permission_overwrite,
    delete_guild_channel,
    delete_guild_role,
    edit_guild_channel,
    edit_guild_role,
    edit_message_components,
    exchange_code,
    fetch_bot_role_ids,
    fetch_current_user,
    fetch_guild_channels,
    fetch_guild_channels_detailed,
    fetch_guild_member,
    fetch_guild_roles,
    fetch_guild_roles_detailed,
    fetch_user_guilds,
    has_manage_access,
    put_channel_permission_overwrite,
    refresh_access_token,
    send_channel_message,
)


def test_has_manage_access_true_for_manage_guild() -> None:
    assert has_manage_access(0x20) is True


def test_has_manage_access_true_for_administrator() -> None:
    assert has_manage_access(0x8) is True


def test_has_manage_access_false_for_unrelated_permissions() -> None:
    # SEND_MESSAGES (0x800) only, no manage/admin bit set.
    assert has_manage_access(0x800) is False


def test_has_manage_access_false_for_no_permissions() -> None:
    assert has_manage_access(0) is False


def test_build_authorize_url_contains_expected_params() -> None:
    url = build_authorize_url(
        client_id=123, redirect_uri="https://example.com/auth/callback", state="xyz"
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["123"]
    assert query["redirect_uri"] == ["https://example.com/auth/callback"]
    assert query["state"] == ["xyz"]
    assert query["scope"] == ["identify guilds"]
    assert query["response_type"] == ["code"]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_exchange_code_posts_authorization_code_grant() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={"access_token": "at", "refresh_token": "rt", "expires_in": 604800},
        )

    async with _client(handler) as http:
        tokens = await exchange_code(
            http,
            client_id=123,
            client_secret="secret",
            redirect_uri="https://example.com/auth/callback",
            code="the-code",
        )

    assert tokens.access_token == "at"
    assert tokens.refresh_token == "rt"
    assert tokens.expires_in == 604800
    assert captured["url"].endswith("/oauth2/token")
    assert "grant_type=authorization_code" in captured["body"]
    assert "code=the-code" in captured["body"]
    assert "client_secret=secret" in captured["body"]


async def test_refresh_access_token_posts_refresh_grant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert "grant_type=refresh_token" in body
        assert "refresh_token=old-refresh" in body
        return httpx.Response(
            200, json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 604800}
        )

    async with _client(handler) as http:
        tokens = await refresh_access_token(
            http, client_id=123, client_secret="secret", refresh_token="old-refresh"
        )

    assert tokens.access_token == "new-at"


async def test_exchange_code_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with _client(handler) as http:
        with pytest.raises(DiscordAPIError):
            await exchange_code(
                http,
                client_id=123,
                client_secret="secret",
                redirect_uri="https://example.com/auth/callback",
                code="bad-code",
            )


async def test_fetch_current_user_sends_bearer_token() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"id": "42", "username": "volvo", "avatar": "abc123"}
        )

    async with _client(handler) as http:
        user = await fetch_current_user(http, "the-access-token")

    assert captured["auth"] == "Bearer the-access-token"
    assert user.id == 42
    assert user.username == "volvo"
    assert user.avatar == "abc123"


async def test_fetch_user_guilds_parses_string_permissions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "111", "name": "Guild A", "icon": None, "permissions": "32"},
                {"id": "222", "name": "Guild B", "icon": "icon-hash", "permissions": "2048"},
            ],
        )

    async with _client(handler) as http:
        guilds = await fetch_user_guilds(http, "the-access-token")

    assert len(guilds) == 2
    assert guilds[0].id == 111
    assert guilds[0].permissions == 32
    assert guilds[1].icon == "icon-hash"


async def test_fetch_user_guilds_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    async with _client(handler) as http:
        with pytest.raises(DiscordAPIError):
            await fetch_user_guilds(http, "bad-token")


async def test_fetch_guild_roles_uses_bot_auth_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json=[
                {"id": "1", "name": "@everyone", "position": 0, "managed": False},
                {"id": "2", "name": "Bot Role", "position": 5, "managed": True},
                {"id": "3", "name": "Moderator", "position": 3, "managed": False},
            ],
        )

    async with _client(handler) as http:
        roles = await fetch_guild_roles(http, "bot-token", 999)

    assert captured["auth"] == "Bot bot-token"
    assert captured["url"].endswith("/guilds/999/roles")
    assert len(roles) == 3
    assert roles[1].managed is True
    assert roles[2].position == 3


async def test_fetch_guild_roles_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "missing access"})

    async with _client(handler) as http:
        with pytest.raises(DiscordAPIError):
            await fetch_guild_roles(http, "bot-token", 999)


async def test_fetch_guild_channels_parses_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "10", "name": "general", "type": 0},
                {"id": "11", "name": "Voice", "type": 2},
            ],
        )

    async with _client(handler) as http:
        channels = await fetch_guild_channels(http, "bot-token", 999)

    assert len(channels) == 2
    assert channels[0].type == 0
    assert channels[1].type == 2


async def test_fetch_bot_role_ids_returns_int_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/members/555")
        return httpx.Response(200, json={"roles": ["2", "3"]})

    async with _client(handler) as http:
        role_ids = await fetch_bot_role_ids(http, "bot-token", 999, 555)

    assert role_ids == [2, 3]


async def test_fetch_guild_member_prefers_nick_then_global_name_then_username() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/members/777")
        return httpx.Response(
            200,
            json={"nick": "The Nick", "user": {"id": "777", "username": "raw-username", "global_name": "Global Name"}},
        )

    async with _client(handler) as http:
        member = await fetch_guild_member(http, "bot-token", 999, 777)

    assert member is not None
    assert member.id == 777
    assert member.username == "raw-username"
    assert member.display_name == "The Nick"


async def test_fetch_guild_member_falls_back_to_global_name_without_nick() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"nick": None, "user": {"id": "777", "username": "raw-username", "global_name": "Global Name"}}
        )

    async with _client(handler) as http:
        member = await fetch_guild_member(http, "bot-token", 999, 777)

    assert member is not None
    assert member.display_name == "Global Name"


async def test_fetch_guild_member_falls_back_to_username_without_nick_or_global_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nick": None, "user": {"id": "777", "username": "raw-username", "global_name": None}})

    async with _client(handler) as http:
        member = await fetch_guild_member(http, "bot-token", 999, 777)

    assert member is not None
    assert member.display_name == "raw-username"


async def test_fetch_guild_member_returns_none_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Unknown Member"})

    async with _client(handler) as http:
        member = await fetch_guild_member(http, "bot-token", 999, 777)

    assert member is None


async def test_fetch_guild_member_raises_on_other_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    async with _client(handler) as http:
        with pytest.raises(DiscordAPIError):
            await fetch_guild_member(http, "bot-token", 999, 777)


def test_bot_top_role_position_returns_zero_when_no_roles() -> None:
    roles = [DiscordRole(id=1, name="@everyone", position=0, managed=False)]
    assert bot_top_role_position(roles, bot_role_ids=[]) == 0


def test_bot_top_role_position_returns_highest_matching_position() -> None:
    roles = [
        DiscordRole(id=1, name="@everyone", position=0, managed=False),
        DiscordRole(id=2, name="Bot Role", position=5, managed=True),
        DiscordRole(id=3, name="Moderator", position=3, managed=False),
    ]
    assert bot_top_role_position(roles, bot_role_ids=[2]) == 5
    assert bot_top_role_position(roles, bot_role_ids=[3]) == 3
    assert bot_top_role_position(roles, bot_role_ids=[2, 3]) == 5


async def test_send_channel_message_posts_content_only_by_default() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "555"})

    async with _client(handler) as http:
        message_id = await send_channel_message(http, "bot-token", 600, content="hello")

    assert message_id == 555
    assert captured["url"].endswith("/channels/600/messages")
    assert captured["auth"] == "Bot bot-token"
    assert captured["body"] == {"content": "hello"}


async def test_send_channel_message_includes_components_when_given() -> None:
    captured: dict = {}
    components = [{"type": 1, "components": [{"type": 2, "style": 1, "custom_id": "x"}]}]

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "555"})

    async with _client(handler) as http:
        await send_channel_message(http, "bot-token", 600, content="hello", components=components)

    assert captured["body"] == {"content": "hello", "components": components}


async def test_send_channel_message_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403, json={"error": "forbidden"})) as http:
        with pytest.raises(DiscordAPIError):
            await send_channel_message(http, "bot-token", 600, content="hello")


async def test_edit_message_components_sends_only_components_key() -> None:
    captured: dict = {}
    components = [{"type": 1, "components": []}]

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "555"})

    async with _client(handler) as http:
        await edit_message_components(http, "bot-token", 600, 555, components)

    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/channels/600/messages/555")
    assert captured["body"] == {"components": components}


async def test_edit_message_components_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(404, json={"error": "unknown message"})) as http:
        with pytest.raises(DiscordAPIError):
            await edit_message_components(http, "bot-token", 600, 555, [])


async def test_add_own_reaction_puts_to_expected_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        # httpx's .path decodes percent-escapes for convenience access - this
        # confirms the emoji round-trips correctly through quote(safe=""),
        # not the raw wire bytes (which would show %3A for each ':').
        captured["path"] = request.url.path
        captured["raw_target"] = request.url.raw_path
        return httpx.Response(204)

    async with _client(handler) as http:
        await add_own_reaction(http, "bot-token", 600, 555, ":catjam:123")

    assert captured["method"] == "PUT"
    assert captured["path"].endswith("/channels/600/messages/555/reactions/:catjam:123/@me")
    # And on the actual wire, the colons are genuinely percent-encoded, not
    # left as literal ':' characters that could be misparsed as path/port
    # separators.
    assert b"%3A" in captured["raw_target"]


async def test_add_own_reaction_raises_on_non_204() -> None:
    async with _client(lambda r: httpx.Response(400, json={"error": "bad emoji"})) as http:
        with pytest.raises(DiscordAPIError):
            await add_own_reaction(http, "bot-token", 600, 555, "🎮")


async def test_clear_reaction_treats_404_as_success() -> None:
    async with _client(lambda r: httpx.Response(404)) as http:
        await clear_reaction(http, "bot-token", 600, 555, "🎮")  # should not raise


async def test_clear_reaction_no_at_me_suffix() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    async with _client(handler) as http:
        await clear_reaction(http, "bot-token", 600, 555, "🎮")

    assert captured["method"] == "DELETE"
    assert not captured["path"].endswith("/@me")


async def test_clear_reaction_raises_on_other_error_status() -> None:
    async with _client(lambda r: httpx.Response(403, json={"error": "forbidden"})) as http:
        with pytest.raises(DiscordAPIError):
            await clear_reaction(http, "bot-token", 600, 555, "🎮")


# --- Server management: roles/channels/overwrites ---


def _role_payload(**overrides: object) -> dict:
    payload = {
        "id": "3", "name": "Moderator", "position": 3, "managed": False,
        "color": 0, "hoist": True, "mentionable": False, "permissions": "8",
    }
    payload.update(overrides)
    return payload


def _channel_payload(**overrides: object) -> dict:
    payload = {
        "id": "10", "name": "general", "type": 0, "position": 1,
        "parent_id": None, "topic": None, "nsfw": False, "rate_limit_per_user": 0,
        "bitrate": None, "user_limit": None, "permission_overwrites": [],
    }
    payload.update(overrides)
    return payload


async def test_fetch_guild_roles_detailed_parses_all_fields() -> None:
    async with _client(lambda r: httpx.Response(200, json=[_role_payload()])) as http:
        roles = await fetch_guild_roles_detailed(http, "bot-token", 999)

    assert roles[0].id == 3
    assert roles[0].hoist is True
    assert roles[0].permissions == 8


async def test_fetch_guild_roles_detailed_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await fetch_guild_roles_detailed(http, "bot-token", 999)


async def test_fetch_guild_channels_detailed_parses_overwrites_and_skips_member_type() -> None:
    payload = _channel_payload(
        parent_id="5",
        permission_overwrites=[
            {"id": "3", "type": 0, "allow": "1024", "deny": "0"},
            {"id": "42", "type": 1, "allow": "0", "deny": "0"},  # member overwrite - out of scope
        ],
    )

    async with _client(lambda r: httpx.Response(200, json=[payload])) as http:
        channels = await fetch_guild_channels_detailed(http, "bot-token", 999)

    assert channels[0].parent_id == 5
    assert channels[0].overwrites == [DiscordPermissionOverwrite(role_id=3, allow=1024, deny=0)]


async def test_fetch_guild_channels_detailed_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await fetch_guild_channels_detailed(http, "bot-token", 999)


async def test_create_guild_role_posts_expected_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_role_payload(name="Staff", permissions="8"))

    async with _client(handler) as http:
        role = await create_guild_role(
            http, "bot-token", 999, name="Staff", hoist=True, permissions=8
        )

    assert captured["url"].endswith("/guilds/999/roles")
    assert captured["body"] == {
        "name": "Staff", "color": 0, "hoist": True, "mentionable": False, "permissions": "8",
    }
    assert role.name == "Staff"


async def test_create_guild_role_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await create_guild_role(http, "bot-token", 999, name="Staff")


async def test_edit_guild_role_patches_expected_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_role_payload())

    async with _client(handler) as http:
        await edit_guild_role(
            http, "bot-token", 999, 3, name="Moderator", color=0, hoist=True, mentionable=False, permissions=8
        )

    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/guilds/999/roles/3")


async def test_edit_guild_role_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(404)) as http:
        with pytest.raises(DiscordAPIError):
            await edit_guild_role(
                http, "bot-token", 999, 3, name="x", color=0, hoist=False, mentionable=False, permissions=0
            )


async def test_delete_guild_role_expects_204() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(204)

    async with _client(handler) as http:
        await delete_guild_role(http, "bot-token", 999, 3)

    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/guilds/999/roles/3")


async def test_delete_guild_role_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await delete_guild_role(http, "bot-token", 999, 3)


async def test_bulk_edit_role_positions_sends_full_array_in_one_call() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[_role_payload()])

    async with _client(handler) as http:
        await bulk_edit_role_positions(http, "bot-token", 999, [(3, 2), (4, 1)])

    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/guilds/999/roles")
    assert captured["body"] == [{"id": "3", "position": 2}, {"id": "4", "position": 1}]


async def test_bulk_edit_role_positions_noop_for_empty_list() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call Discord for an empty move list")

    async with _client(fail) as http:
        await bulk_edit_role_positions(http, "bot-token", 999, [])


async def test_bulk_edit_role_positions_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await bulk_edit_role_positions(http, "bot-token", 999, [(3, 2)])


async def test_create_guild_channel_includes_overwrites_inline() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_channel_payload())

    overwrites = [DiscordPermissionOverwrite(role_id=999, allow=0, deny=1024)]
    async with _client(handler) as http:
        await create_guild_channel(
            http, "bot-token", 999, name="general", type=0, overwrites=overwrites
        )

    assert captured["body"]["permission_overwrites"] == [
        {"id": "999", "type": 0, "allow": "0", "deny": "1024"}
    ]


async def test_create_guild_channel_omits_text_only_fields_for_voice() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_channel_payload(type=2))

    async with _client(handler) as http:
        await create_guild_channel(http, "bot-token", 999, name="Voice", type=2, bitrate=64000)

    assert "nsfw" not in captured["body"]
    assert captured["body"]["bitrate"] == 64000


async def test_create_guild_channel_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await create_guild_channel(http, "bot-token", 999, name="general", type=0)


async def test_edit_guild_channel_patches_channel_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_channel_payload())

    async with _client(handler) as http:
        await edit_guild_channel(
            http, "bot-token", 10, name="general", parent_id=None, topic=None,
            nsfw=False, rate_limit_per_user=0, bitrate=None, user_limit=None,
        )

    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/channels/10")


async def test_edit_guild_channel_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(404)) as http:
        with pytest.raises(DiscordAPIError):
            await edit_guild_channel(
                http, "bot-token", 10, name="x", parent_id=None, topic=None,
                nsfw=False, rate_limit_per_user=0, bitrate=None, user_limit=None,
            )


async def test_delete_guild_channel_expects_200_with_body() -> None:
    # Discord's channel-delete endpoint uniquely returns 200 with the
    # deleted channel object, not 204 like role delete.
    async with _client(lambda r: httpx.Response(200, json=_channel_payload())) as http:
        await delete_guild_channel(http, "bot-token", 10)  # should not raise


async def test_delete_guild_channel_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await delete_guild_channel(http, "bot-token", 10)


async def test_bulk_edit_channel_positions_sends_full_array_in_one_call() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path.endswith("/guilds/999/channels")
        captured["body"] = json.loads(request.content)
        return httpx.Response(204)

    async with _client(handler) as http:
        await bulk_edit_channel_positions(http, "bot-token", 999, [(10, 0, None), (11, 1, 20)])

    assert captured["body"] == [
        {"id": "10", "position": 0, "parent_id": None},
        {"id": "11", "position": 1, "parent_id": "20"},
    ]


async def test_bulk_edit_channel_positions_noop_for_empty_list() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call Discord for an empty move list")

    async with _client(fail) as http:
        await bulk_edit_channel_positions(http, "bot-token", 999, [])


async def test_bulk_edit_channel_positions_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(400)) as http:
        with pytest.raises(DiscordAPIError):
            await bulk_edit_channel_positions(http, "bot-token", 999, [(10, 0, None)])


async def test_put_channel_permission_overwrite_sends_expected_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(204)

    async with _client(handler) as http:
        await put_channel_permission_overwrite(http, "bot-token", 10, role_id=3, allow=1024, deny=0)

    assert captured["method"] == "PUT"
    assert captured["url"].endswith("/channels/10/permissions/3")
    assert captured["body"] == {"allow": "1024", "deny": "0", "type": 0}


async def test_put_channel_permission_overwrite_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await put_channel_permission_overwrite(http, "bot-token", 10, role_id=3, allow=0, deny=0)


async def test_delete_channel_permission_overwrite_expects_204() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(204)

    async with _client(handler) as http:
        await delete_channel_permission_overwrite(http, "bot-token", 10, 3)

    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/channels/10/permissions/3")


async def test_delete_channel_permission_overwrite_raises_on_error_status() -> None:
    async with _client(lambda r: httpx.Response(403)) as http:
        with pytest.raises(DiscordAPIError):
            await delete_channel_permission_overwrite(http, "bot-token", 10, 3)
