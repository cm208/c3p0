from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit_log_service import AuditLogService
from app.services.builtin_templates import (
    EVERYONE_ROLE_NAME,
    TemplateChannelDef,
    TemplateDefinition,
    TemplateOverwriteDef,
    TemplateRoleDef,
)
from app.web.discord_client import (
    DiscordChannelDetail,
    DiscordPermissionOverwrite,
    DiscordRoleDetail,
)
from app.web.server_management import (
    RoleReorderValidationError,
    apply_template,
    build_definition_from_live_state,
    resolve_role_reorder,
)

GUILD_A = 111

_VIEW_CHANNEL = 0x400
_KICK_MEMBERS = 0x2
_MANAGE_GUILD = 0x20


def _role(id: int, name: str, *, managed: bool = False, permissions: int = 0, position: int = 1) -> DiscordRoleDetail:
    return DiscordRoleDetail(
        id=id, name=name, position=position, managed=managed, color=0, hoist=False, mentionable=False,
        permissions=permissions,
    )


def _channel(
    id: int, name: str, *, type: int = 0, parent_id: int | None = None,
    overwrites: list[DiscordPermissionOverwrite] | None = None,
) -> DiscordChannelDetail:
    return DiscordChannelDetail(
        id=id, name=name, type=type, position=1, parent_id=parent_id, topic=None, nsfw=False,
        rate_limit_per_user=0, bitrate=None, user_limit=None, overwrites=overwrites or [],
    )


# --- build_definition_from_live_state ---


def test_build_definition_excludes_everyone_and_managed_roles() -> None:
    roles = [
        _role(GUILD_A, "@everyone"),
        _role(500, "Staff", permissions=_KICK_MEMBERS),
        _role(501, "Bot Integration", managed=True),
    ]

    definition = build_definition_from_live_state(GUILD_A, roles, [])

    assert [r.name for r in definition.roles] == ["Staff"]
    assert definition.roles[0].permissions == _KICK_MEMBERS


def test_build_definition_maps_parent_and_overwrite_names() -> None:
    roles = [_role(GUILD_A, "@everyone"), _role(500, "Staff")]
    channels = [
        _channel(700, "Staff Area", type=4),
        _channel(
            701, "mod-chat", parent_id=700,
            overwrites=[
                DiscordPermissionOverwrite(role_id=GUILD_A, allow=0, deny=_VIEW_CHANNEL),
                DiscordPermissionOverwrite(role_id=500, allow=_VIEW_CHANNEL, deny=0),
            ],
        ),
    ]

    definition = build_definition_from_live_state(GUILD_A, roles, channels)

    mod_chat = next(c for c in definition.channels if c.name == "mod-chat")
    assert mod_chat.parent_name == "Staff Area"
    overwrite_by_role = {o.role_name: o for o in mod_chat.overwrites}
    assert overwrite_by_role[EVERYONE_ROLE_NAME].deny == _VIEW_CHANNEL
    assert overwrite_by_role["Staff"].allow == _VIEW_CHANNEL


def test_build_definition_drops_overwrite_referencing_managed_role() -> None:
    roles = [_role(GUILD_A, "@everyone"), _role(999, "Bot Role", managed=True)]
    channels = [_channel(701, "general", overwrites=[DiscordPermissionOverwrite(role_id=999, allow=1, deny=0)])]

    definition = build_definition_from_live_state(GUILD_A, roles, channels)

    assert definition.channels[0].overwrites == ()


# --- apply_template ---


def _handler_factory(*, fail_channel_names: set[str] | None = None, fail_delete_channel_ids: set[int] | None = None):
    fail_channel_names = fail_channel_names or set()
    fail_delete_channel_ids = fail_delete_channel_ids or set()
    next_role_id = {"value": 9000}
    next_channel_id = {"value": 9500}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "DELETE" and "/channels/" in path and "/permissions/" not in path:
            channel_id = int(path.rsplit("/", 1)[-1])
            if channel_id in fail_delete_channel_ids:
                return httpx.Response(400, json={"error": "cannot delete"})
            return httpx.Response(200, json={"id": str(channel_id)})
        if request.method == "POST" and path.endswith("/roles"):
            body = json.loads(request.content)
            role_id = next_role_id["value"]
            next_role_id["value"] += 1
            return httpx.Response(
                200,
                json={
                    "id": str(role_id), "name": body["name"], "position": 1, "managed": False,
                    "color": body["color"], "hoist": body["hoist"], "mentionable": body["mentionable"],
                    "permissions": body["permissions"],
                },
            )
        if request.method == "POST" and path.endswith("/channels"):
            body = json.loads(request.content)
            if body["name"] in fail_channel_names:
                return httpx.Response(400, json={"error": "rate limited"})
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
        raise AssertionError(f"unexpected call: {request.method} {request.url}")

    return handler


async def test_apply_template_creates_roles_and_channels_and_records_audit(db_session: AsyncSession) -> None:
    definition = TemplateDefinition(
        roles=(TemplateRoleDef(name="Staff", permissions=_KICK_MEMBERS),),
        channels=(
            TemplateChannelDef(name="Staff Area", type=4),
            TemplateChannelDef(
                name="mod-chat", type=0, parent_name="Staff Area",
                overwrites=(
                    TemplateOverwriteDef(role_name=EVERYONE_ROLE_NAME, deny=_VIEW_CHANNEL),
                    TemplateOverwriteDef(role_name="Staff", allow=_VIEW_CHANNEL),
                ),
            ),
        ),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="Community Starter",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD | _KICK_MEMBERS | _VIEW_CHANNEL,
            current_roles=[], current_channels=[],
        )

    assert [(r.kind, r.name, r.status) for r in results] == [
        ("role", "Staff", "created"),
        ("channel", "Staff Area", "created"),
        ("channel", "mod-chat", "created"),
    ]
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 3
    assert all("Community Starter" in e.summary for e in entries)


async def test_apply_template_skips_existing_role_but_wipes_and_recreates_channel(db_session: AsyncSession) -> None:
    # Roles stay create-only/skip-on-conflict; channels are wiped first, so
    # an existing "general" channel is deleted then recreated fresh rather
    # than being treated as a name conflict.
    definition = TemplateDefinition(
        roles=(TemplateRoleDef(name="Staff"),),
        channels=(TemplateChannelDef(name="general", type=0),),
    )
    current_roles = [_role(500, "Staff")]
    current_channels = [_channel(700, "general")]

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,
            current_roles=current_roles, current_channels=current_channels,
        )

    assert [(r.kind, r.name, r.status) for r in results] == [
        ("channel", "general", "deleted"),
        ("role", "Staff", "skipped_conflict"),
        ("channel", "general", "created"),
    ]
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 2
    assert {e.action.value for e in entries} == {"channel_delete", "channel_create"}


async def test_apply_template_wipes_every_existing_channel_regardless_of_template(db_session: AsyncSession) -> None:
    # A channel/category with no name overlap with the template at all is
    # still deleted - the wipe is unconditional, not name-matched.
    definition = TemplateDefinition(channels=(TemplateChannelDef(name="new-channel", type=0),))
    current_channels = [_channel(700, "unrelated-one"), _channel(701, "unrelated-two", type=4)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,
            current_roles=[], current_channels=current_channels,
        )

    deleted = [r for r in results if r.status == "deleted"]
    assert {r.name for r in deleted} == {"unrelated-one", "unrelated-two"}
    created = [r for r in results if r.status == "created"]
    assert [r.name for r in created] == ["new-channel"]


async def test_apply_template_continues_to_create_phase_when_a_delete_fails(db_session: AsyncSession) -> None:
    # A channel that fails to delete stays "taken" - the create phase
    # correctly reports it as a conflict instead of trying to duplicate it.
    definition = TemplateDefinition(channels=(TemplateChannelDef(name="general", type=0),))
    current_channels = [_channel(700, "general")]

    handler = _handler_factory(fail_delete_channel_ids={700})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,
            current_roles=[], current_channels=current_channels,
        )

    assert [(r.kind, r.name, r.status) for r in results] == [
        ("channel", "general", "failed"),
        ("channel", "general", "skipped_conflict"),
    ]
    assert await AuditLogService().list_for_guild(GUILD_A) == []


async def test_apply_template_rejects_permission_operator_lacks_but_continues(db_session: AsyncSession) -> None:
    definition = TemplateDefinition(
        roles=(
            TemplateRoleDef(name="Overpowered", permissions=_KICK_MEMBERS),
            TemplateRoleDef(name="Plain"),
        ),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,  # no kick_members
            current_roles=[], current_channels=[],
        )

    assert results[0].status == "failed"
    assert results[1].status == "created"
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].target_name == "Plain"


async def test_apply_template_reports_partial_completion_on_discord_error(db_session: AsyncSession) -> None:
    definition = TemplateDefinition(
        channels=(
            TemplateChannelDef(name="good-channel", type=0),
            TemplateChannelDef(name="bad-channel", type=0),
        ),
    )

    handler = _handler_factory(fail_channel_names={"bad-channel"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,
            current_roles=[], current_channels=[],
        )

    assert [(r.name, r.status) for r in results] == [("good-channel", "created"), ("bad-channel", "failed")]
    entries = await AuditLogService().list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].target_name == "good-channel"


async def test_apply_template_resolves_categories_before_children_regardless_of_order(db_session: AsyncSession) -> None:
    # Child listed before its category in the definition - apply_template
    # must still process categories first so parent_name resolves.
    definition = TemplateDefinition(
        channels=(
            TemplateChannelDef(name="general", type=0, parent_name="General"),
            TemplateChannelDef(name="General", type=4),
        ),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory())) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,
            current_roles=[], current_channels=[],
        )

    assert all(r.status == "created" for r in results)


async def test_apply_template_drops_overwrite_for_role_that_failed_to_create(db_session: AsyncSession) -> None:
    definition = TemplateDefinition(
        roles=(TemplateRoleDef(name="Locked", permissions=_KICK_MEMBERS),),  # operator lacks this -> fails
        channels=(
            TemplateChannelDef(
                name="general", type=0,
                overwrites=(TemplateOverwriteDef(role_name="Locked", allow=_VIEW_CHANNEL),),
            ),
        ),
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/channels"):
            body = json.loads(request.content)
            captured["overwrites"] = body.get("permission_overwrites", [])
            return httpx.Response(
                201,
                json={
                    "id": "9999", "name": body["name"], "type": 0, "position": 1, "parent_id": None,
                    "topic": None, "nsfw": False, "rate_limit_per_user": 0, "bitrate": None, "user_limit": None,
                    "permission_overwrites": body.get("permission_overwrites", []),
                },
            )
        raise AssertionError("role creation should not have been attempted to succeed for this test")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await apply_template(
            http, "bot-token", GUILD_A, definition=definition, template_label="X",
            actor_discord_user_id=42, operator_permission_bits=_MANAGE_GUILD,  # no kick_members
            current_roles=[], current_channels=[],
        )

    assert results[0].status == "failed"  # the role
    assert results[1].status == "created"  # the channel, without the dropped overwrite
    assert captured["overwrites"] == []


# --- resolve_role_reorder ---


def test_resolve_role_reorder_permutes_only_the_editable_positions() -> None:
    # Managed (100) and above-C3P0 (300) roles hold positions 5 and 4 -
    # editable roles (10, 20) hold 2 and 1. Reordering the editable pair
    # must only ever reassign 2 and 1 between them, never touch 5 or 4.
    roles = [
        _role(300, "Owner-only", position=4),
        _role(100, "Integration", managed=True, position=5),
        _role(10, "Alpha", position=1),
        _role(20, "Beta", position=2),
    ]
    moves = resolve_role_reorder([20, 10], current_roles=roles, editable_role_ids={10, 20})
    assert moves == [(20, 2), (10, 1)]


def test_resolve_role_reorder_noop_order_returns_identical_positions() -> None:
    roles = [_role(10, "Alpha", position=1), _role(20, "Beta", position=2)]
    moves = resolve_role_reorder([20, 10], current_roles=roles, editable_role_ids={10, 20})
    assert moves == [(20, 2), (10, 1)]


def test_resolve_role_reorder_rejects_order_missing_an_editable_role() -> None:
    roles = [_role(10, "Alpha", position=1), _role(20, "Beta", position=2)]
    with pytest.raises(RoleReorderValidationError):
        resolve_role_reorder([10], current_roles=roles, editable_role_ids={10, 20})


def test_resolve_role_reorder_rejects_order_including_a_non_editable_role() -> None:
    roles = [_role(10, "Alpha", position=1), _role(20, "Beta", position=2, managed=True)]
    with pytest.raises(RoleReorderValidationError):
        resolve_role_reorder([10, 20], current_roles=roles, editable_role_ids={10})
