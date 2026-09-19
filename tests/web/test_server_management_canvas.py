from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit_log_service import AuditLogService
from app.web.discord_client import DiscordChannelDetail, DiscordPermissionOverwrite
from app.web.server_management import (
    CanvasBatchValidationError,
    apply_channel_canvas_batch,
    parse_channel_canvas_batch,
)

GUILD_A = 111
_VIEW_CHANNEL = 0x400
_MANAGE_GUILD = 0x20


def _channel(
    id: int, name: str, *, type: int = 0, parent_id: int | None = None,
    overwrites: list[DiscordPermissionOverwrite] | None = None,
) -> DiscordChannelDetail:
    return DiscordChannelDetail(
        id=id, name=name, type=type, position=1, parent_id=parent_id, topic=None, nsfw=False,
        rate_limit_per_user=0, bitrate=None, user_limit=None, overwrites=overwrites or [],
    )


# --- parse_channel_canvas_batch ---


def test_parse_empty_batch() -> None:
    items, moves = parse_channel_canvas_batch({})
    assert items == []
    assert moves == []


def test_parse_create_item_round_trips_fields() -> None:
    items, _moves = parse_channel_canvas_batch(
        {
            "channels": [
                {
                    "op": "create", "temp_id": "new-1", "type": 0, "name": "general", "topic": "chat",
                    "parent": {"kind": "temp", "id": "new-0"},
                    "overwrites": [{"role_id": 500, "allow": 1, "deny": 0}],
                }
            ]
        }
    )
    assert len(items) == 1
    item = items[0]
    assert item.op == "create"
    assert item.temp_id == "new-1"
    assert item.type == 0
    assert item.name == "general"
    assert item.topic == "chat"
    assert item.parent.kind == "temp"
    assert item.parent.value == "new-0"
    assert item.overwrites[0].role_id == 500


def test_parse_rejects_missing_op() -> None:
    with pytest.raises(CanvasBatchValidationError):
        parse_channel_canvas_batch({"channels": [{"name": "x"}]})


def test_parse_rejects_create_without_temp_id() -> None:
    with pytest.raises(CanvasBatchValidationError):
        parse_channel_canvas_batch({"channels": [{"op": "create", "type": 0, "name": "x"}]})


def test_parse_rejects_create_with_invalid_type() -> None:
    with pytest.raises(CanvasBatchValidationError):
        parse_channel_canvas_batch({"channels": [{"op": "create", "temp_id": "a", "type": 99, "name": "x"}]})


def test_parse_rejects_edit_without_id() -> None:
    with pytest.raises(CanvasBatchValidationError):
        parse_channel_canvas_batch({"channels": [{"op": "edit", "name": "x"}]})


def test_parse_delete_does_not_require_name() -> None:
    items, _moves = parse_channel_canvas_batch({"channels": [{"op": "delete", "id": 700}]})
    assert items[0].id == 700


def test_parse_positions_round_trip() -> None:
    _items, moves = parse_channel_canvas_batch(
        {"positions": [{"ref": {"kind": "existing", "id": 700}, "position": 2, "parent": {"kind": "temp", "id": "new-1"}}]}
    )
    assert len(moves) == 1
    assert moves[0].ref.kind == "existing"
    assert moves[0].ref.value == 700
    assert moves[0].position == 2
    assert moves[0].parent.value == "new-1"


def test_parse_rejects_position_without_ref() -> None:
    with pytest.raises(CanvasBatchValidationError):
        parse_channel_canvas_batch({"positions": [{"position": 1}]})


def test_parse_rejects_non_dict_payload() -> None:
    with pytest.raises(CanvasBatchValidationError):
        parse_channel_canvas_batch(["not", "a", "dict"])


# --- apply_channel_canvas_batch ---


def _handler_factory(*, fail_names: set[str] | None = None):
    fail_names = fail_names or set()
    next_channel_id = {"value": 9500}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if method == "POST" and path.endswith("/channels"):
            body = json.loads(request.content)
            if body["name"] in fail_names:
                return httpx.Response(400, json={"error": "bad"})
            channel_id = next_channel_id["value"]
            next_channel_id["value"] += 1
            return httpx.Response(
                201,
                json={
                    "id": str(channel_id), "name": body["name"], "type": body["type"], "position": 1,
                    "parent_id": body.get("parent_id"), "topic": body.get("topic"), "nsfw": body.get("nsfw", False),
                    "rate_limit_per_user": body.get("rate_limit_per_user", 0), "bitrate": body.get("bitrate"),
                    "user_limit": body.get("user_limit"), "permission_overwrites": body.get("permission_overwrites", []),
                },
            )
        if method == "PATCH" and "/channels/" in path and path.rsplit("/", 1)[-1].isdigit():
            channel_id = int(path.rsplit("/", 1)[-1])
            body = json.loads(request.content)
            if body.get("name") in fail_names:
                return httpx.Response(400, json={"error": "bad"})
            return httpx.Response(
                200,
                json={
                    "id": str(channel_id), "name": body.get("name", "renamed"), "type": 0, "position": 1,
                    "parent_id": body.get("parent_id"), "topic": body.get("topic"), "nsfw": body.get("nsfw", False),
                    "rate_limit_per_user": body.get("rate_limit_per_user", 0), "bitrate": body.get("bitrate"),
                    "user_limit": body.get("user_limit"), "permission_overwrites": [],
                },
            )
        if method == "DELETE" and "/channels/" in path and "/permissions/" not in path:
            channel_id = path.rsplit("/", 1)[-1]
            if channel_id in fail_names:
                return httpx.Response(400, json={"error": "bad"})
            return httpx.Response(200, json={"id": channel_id})
        if method == "PUT" and "/permissions/" in path:
            return httpx.Response(204)
        if method == "DELETE" and "/permissions/" in path:
            return httpx.Response(204)
        if method == "PATCH" and path.endswith(f"/guilds/{GUILD_A}/channels"):
            return httpx.Response(204)
        raise AssertionError(f"unexpected call: {method} {request.url}")

    return handler


async def test_apply_canvas_batch_creates_category_before_child(db_session: AsyncSession) -> None:
    items, _moves = parse_channel_canvas_batch(
        {
            "channels": [
                {"op": "create", "temp_id": "child", "type": 0, "name": "general", "parent": {"kind": "temp", "id": "cat"}},
                {"op": "create", "temp_id": "cat", "type": 4, "name": "General"},
            ]
        }
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=[], actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=[],
        )

    assert [(r.name, r.status) for r in results] == [("General", "created"), ("general", "created")]
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 2


async def test_apply_canvas_batch_rejects_overwrite_operator_lacks(db_session: AsyncSession) -> None:
    items, _moves = parse_channel_canvas_batch(
        {
            "channels": [
                {
                    "op": "create", "temp_id": "a", "type": 0, "name": "locked",
                    "overwrites": [{"role_id": 500, "allow": _VIEW_CHANNEL, "deny": 0}],
                }
            ]
        }
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=[], actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=[],
        )

    assert results[0].status == "failed"
    assert await AuditLogService().list_for_guild(GUILD_A) == []


async def test_apply_canvas_batch_edits_existing_channel(db_session: AsyncSession) -> None:
    current = [_channel(700, "old-name")]
    items, _moves = parse_channel_canvas_batch({"channels": [{"op": "edit", "id": 700, "name": "new-name"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=[], actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=current,
        )

    assert [(r.status, r.name) for r in results] == [("edited", "new-name")]


async def test_apply_canvas_batch_edit_missing_channel_fails_gracefully(db_session: AsyncSession) -> None:
    items, _moves = parse_channel_canvas_batch({"channels": [{"op": "edit", "id": 999, "name": "ghost"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=[], actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=[],
        )

    assert results[0].status == "failed"
    assert "no longer exists" in results[0].detail


async def test_apply_canvas_batch_deletes_existing_channel(db_session: AsyncSession) -> None:
    current = [_channel(700, "to-remove")]
    items, _moves = parse_channel_canvas_batch({"channels": [{"op": "delete", "id": 700}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=[], actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=current,
        )

    assert [(r.status, r.name) for r in results] == [("deleted", "to-remove")]


async def test_apply_canvas_batch_drops_move_for_channel_deleted_in_same_batch(db_session: AsyncSession) -> None:
    current = [_channel(700, "gone")]
    items, moves = parse_channel_canvas_batch(
        {
            "channels": [{"op": "delete", "id": 700}],
            "positions": [{"ref": {"kind": "existing", "id": 700}, "position": 0}],
        }
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=moves, actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=current,
        )

    assert not any(r.status == "moved" for r in results)


async def test_apply_canvas_batch_resolves_temp_id_move_after_create(db_session: AsyncSession) -> None:
    items, moves = parse_channel_canvas_batch(
        {
            "channels": [{"op": "create", "temp_id": "a", "type": 0, "name": "new-channel"}],
            "positions": [{"ref": {"kind": "temp", "id": "a"}, "position": 3}],
        }
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=moves, actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=[],
        )

    moved = [r for r in results if r.status == "moved"]
    assert len(moved) == 1
    assert "Repositioned 1" in moved[0].detail


async def test_apply_canvas_batch_reports_partial_failure_and_continues(db_session: AsyncSession) -> None:
    items, _moves = parse_channel_canvas_batch(
        {
            "channels": [
                {"op": "create", "temp_id": "a", "type": 0, "name": "good-channel"},
                {"op": "create", "temp_id": "b", "type": 0, "name": "bad-channel"},
            ]
        }
    )

    handler = _handler_factory(fail_names={"bad-channel"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await apply_channel_canvas_batch(
            http, "bot-token", GUILD_A, items=items, moves=[], actor_discord_user_id=42,
            operator_permission_bits=_MANAGE_GUILD, current_channels=[],
        )

    assert [(r.name, r.status) for r in results] == [("good-channel", "created"), ("bad-channel", "failed")]
