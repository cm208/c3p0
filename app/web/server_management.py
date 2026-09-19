"""Template snapshot/apply, and channel-canvas batch apply, for the
server-management feature.

Kept out of app/web/routers/server_management.py so this - the meatiest,
most failure-prone logic in the whole feature - can be unit-tested without
constructing a FastAPI request. Also kept out of app/services/ (unlike
TemplateService/AuditLogService): every function here makes real Discord
REST calls, and discord.py-touching orchestration stays out of the
service layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.db.models.audit_log_entry import AuditAction, AuditTargetType
from app.services.audit_log_service import AuditLogService
from app.services.builtin_templates import (
    CATEGORY_CHANNEL_TYPE,
    EVERYONE_ROLE_NAME,
    TemplateChannelDef,
    TemplateDefinition,
    TemplateOverwriteDef,
    TemplateRoleDef,
)
from app.utils.permissions import has_all_permission_bits
from app.web.discord_client import (
    DiscordAPIError,
    DiscordChannelDetail,
    DiscordPermissionOverwrite,
    DiscordRoleDetail,
    bulk_edit_channel_positions,
    create_guild_channel,
    create_guild_role,
    delete_channel_permission_overwrite,
    delete_guild_channel,
    edit_guild_channel,
    put_channel_permission_overwrite,
)

_CREATABLE_CHANNEL_TYPES = frozenset({0, 2, CATEGORY_CHANNEL_TYPE})


def build_definition_from_live_state(
    guild_id: int, roles: list[DiscordRoleDetail], channels: list[DiscordChannelDetail]
) -> TemplateDefinition:
    """Snapshot a guild's current roles/channels into a portable, name-keyed
    TemplateDefinition.

    Excludes @everyone (implicit, never a role def) and managed roles
    (can't be recreated by us anyway). Channel parent/overwrite references
    are re-expressed by *name*, not id, since ids won't exist yet on
    whatever guild the template is later applied to - an overwrite
    referencing a role this snapshot doesn't export (a managed role) is
    dropped, since it can't be resolved by name on apply.

    Two roles sharing the exact same display name is a known, accepted
    edge case (Discord itself allows duplicate role names) - the snapshot
    keeps whichever one a dict comprehension happens to keep last, matching
    apply_template's own "matched by exact name" contract rather than
    inventing disambiguation the rest of this feature doesn't have either.
    """
    exportable_roles = [r for r in roles if r.id != guild_id and not r.managed]
    role_name_by_id = {r.id: r.name for r in exportable_roles}

    role_defs = tuple(
        TemplateRoleDef(name=r.name, color=r.color, hoist=r.hoist, mentionable=r.mentionable, permissions=r.permissions)
        for r in exportable_roles
    )

    category_name_by_id = {c.id: c.name for c in channels if c.type == CATEGORY_CHANNEL_TYPE}

    channel_defs = []
    for channel in channels:
        overwrite_defs = []
        for overwrite in channel.overwrites:
            if overwrite.role_id == guild_id:
                role_name = EVERYONE_ROLE_NAME
            elif overwrite.role_id in role_name_by_id:
                role_name = role_name_by_id[overwrite.role_id]
            else:
                continue
            overwrite_defs.append(
                TemplateOverwriteDef(role_name=role_name, allow=overwrite.allow, deny=overwrite.deny)
            )
        channel_defs.append(
            TemplateChannelDef(
                name=channel.name,
                type=channel.type,
                parent_name=category_name_by_id.get(channel.parent_id),
                topic=channel.topic,
                nsfw=channel.nsfw,
                rate_limit_per_user=channel.rate_limit_per_user,
                bitrate=channel.bitrate,
                user_limit=channel.user_limit,
                overwrites=tuple(overwrite_defs),
            )
        )
    return TemplateDefinition(roles=role_defs, channels=tuple(channel_defs))


# --- Role canvas: drag-and-drop reorder only ---
#
# Unlike the channel canvas, this doesn't stage creates/edits/deletes - the
# role list's Edit/Delete/New actions stay exactly as they were (separate,
# immediate, full-page-navigation routes). Only *reordering* becomes a
# drag-and-drop draft, since that's the one interaction the numeric
# position-box + Move button genuinely made awkward for anything beyond a
# single move.


class RoleReorderValidationError(Exception):
    """Raised when a submitted reorder doesn't match the guild's current
    editable roles - a stale draft (a role was created/deleted/edited into
    or out of editability since the canvas loaded), not a malformed
    payload. Message is user-safe."""


def resolve_role_reorder(
    order: list[int], *, current_roles: list[DiscordRoleDetail], editable_role_ids: set[int]
) -> list[tuple[int, int]]:
    """Turn a client-submitted top-to-bottom order of editable role ids
    into the (role_id, position) pairs to send Discord.

    Deliberately never trusts a submitted *position number* - only a
    submitted *order*, which is zipped against the positions editable
    roles already hold right now (freshly fetched, not the draft's idea of
    "current"). The result is always an exact permutation of those
    existing values, which makes it impossible - by construction, not by
    UI restriction - for a reordered role to land at or above a
    non-editable (managed, or above C3P0's own role) role's position: a
    non-editable role's position is never in the set being permuted, so it
    can never be handed to one of the roles that are.
    """
    if sorted(order) != sorted(editable_role_ids):
        raise RoleReorderValidationError(
            "This server's roles changed since the page loaded - reload and try again."
        )
    editable_positions = sorted((r.position for r in current_roles if r.id in editable_role_ids), reverse=True)
    return list(zip(order, editable_positions, strict=True))


@dataclass(frozen=True, slots=True)
class BatchItemResult:
    """One item's outcome from a sequential, per-item bulk operation -
    shared between apply_template's wipe-then-create flow and
    apply_channel_canvas_batch's create/edit/delete/reorder flow below,
    since both are the same "no bulk path that skips checks, report
    genuine partial completion" shape the spec requires."""

    kind: str  # "role" or "channel"
    name: str
    status: str  # "created", "edited", "deleted", "moved", "skipped_conflict", or "failed"
    detail: str | None = None


async def apply_template(
    http: httpx.AsyncClient,
    bot_token: str,
    guild_id: int,
    *,
    definition: TemplateDefinition,
    template_label: str,
    actor_discord_user_id: int,
    operator_permission_bits: int,
    current_roles: list[DiscordRoleDetail],
    current_channels: list[DiscordChannelDetail],
) -> list[BatchItemResult]:
    """Wipe the guild's existing channels, then create everything a
    template defines.

    Channels are destructive on purpose: every existing channel and
    category is deleted first (a template apply is meant to reset the
    server's channel layout to the template, not merge into it), and only
    then are the template's own channels created. Roles are the opposite -
    still create-only, matched by exact name, never touched by the wipe -
    since a role can carry permissions/assignments elsewhere in the guild
    that deleting on a channel-layout reset would be a surprising, separate
    blast radius the user never asked for.

    Every created/deleted item goes through the exact same validation a
    single manual create/delete would (the operator-permission-subset
    check on create), processed sequentially so a later item can rely on
    an earlier one's newly created role/category actually existing. A
    DiscordAPIError or a failed validation check on one item is recorded
    and the loop continues - never aborts the whole apply, since a
    template apply that stops on the first failure would silently
    under-deliver with no way to tell the operator "6 of 9 created, then
    this one failed."
    """
    results: list[BatchItemResult] = []
    audit_log = AuditLogService()

    # --- Wipe phase: delete every existing channel/category first. A
    # channel whose delete fails stays in `remaining_channels` so the
    # create phase below still treats its name as taken (skipped_conflict)
    # rather than trying to create a duplicate on top of a delete that
    # didn't actually happen.
    remaining_channels: list[DiscordChannelDetail] = []
    for channel in current_channels:
        try:
            await delete_guild_channel(http, bot_token, channel.id)
        except DiscordAPIError as exc:
            remaining_channels.append(channel)
            results.append(
                BatchItemResult(
                    kind="channel", name=channel.name, status="failed",
                    detail=f"Couldn't delete the existing channel first ({exc}).",
                )
            )
            continue

        await audit_log.record(
            guild_id, actor_discord_user_id=actor_discord_user_id, action=AuditAction.CHANNEL_DELETE,
            target_type=AuditTargetType.CHANNEL, target_id=channel.id, target_name=channel.name,
            summary=f"Deleted channel {channel.name!r} as part of applying template {template_label!r} (full channel wipe).",
        )
        results.append(BatchItemResult(kind="channel", name=channel.name, status="deleted"))

    existing_role_ids_by_name = {r.name: r.id for r in current_roles}
    role_id_by_name: dict[str, int] = dict(existing_role_ids_by_name)

    for role_def in definition.roles:
        if role_def.name in existing_role_ids_by_name:
            results.append(BatchItemResult(kind="role", name=role_def.name, status="skipped_conflict"))
            continue

        if not has_all_permission_bits(held=operator_permission_bits, requested=role_def.permissions):
            results.append(
                BatchItemResult(
                    kind="role", name=role_def.name, status="failed",
                    detail="You don't hold a permission this role would grant.",
                )
            )
            continue

        try:
            created = await create_guild_role(
                http, bot_token, guild_id, name=role_def.name, color=role_def.color,
                hoist=role_def.hoist, mentionable=role_def.mentionable, permissions=role_def.permissions,
            )
        except DiscordAPIError as exc:
            results.append(BatchItemResult(kind="role", name=role_def.name, status="failed", detail=str(exc)))
            continue

        role_id_by_name[role_def.name] = created.id
        await audit_log.record(
            guild_id, actor_discord_user_id=actor_discord_user_id, action=AuditAction.ROLE_CREATE,
            target_type=AuditTargetType.ROLE, target_id=created.id, target_name=created.name,
            summary=f"Created role {created.name!r} via template {template_label!r}.",
        )
        results.append(BatchItemResult(kind="role", name=role_def.name, status="created"))

    # Keyed off `remaining_channels` (post-wipe), not the original
    # `current_channels` - a successfully deleted channel's name is free
    # again; one that failed to delete is still taken.
    existing_channel_names = {c.name for c in remaining_channels}
    category_id_by_name = {c.name: c.id for c in remaining_channels if c.type == CATEGORY_CHANNEL_TYPE}

    # Categories first regardless of definition order, so a child channel's
    # parent_name always resolves to an id that already exists by the time
    # the child itself is processed.
    ordered_channel_defs = [c for c in definition.channels if c.type == CATEGORY_CHANNEL_TYPE]
    ordered_channel_defs += [c for c in definition.channels if c.type != CATEGORY_CHANNEL_TYPE]

    for channel_def in ordered_channel_defs:
        if channel_def.name in existing_channel_names:
            results.append(BatchItemResult(kind="channel", name=channel_def.name, status="skipped_conflict"))
            continue

        parent_id = category_id_by_name.get(channel_def.parent_name) if channel_def.parent_name else None

        overwrites: list[DiscordPermissionOverwrite] = []
        requested_allow_bits = 0
        for overwrite_def in channel_def.overwrites:
            role_id = guild_id if overwrite_def.role_name == EVERYONE_ROLE_NAME else role_id_by_name.get(overwrite_def.role_name)
            if role_id is None:
                continue  # referenced role failed/was never created - drop just this one overwrite
            overwrites.append(DiscordPermissionOverwrite(role_id=role_id, allow=overwrite_def.allow, deny=overwrite_def.deny))
            requested_allow_bits |= overwrite_def.allow

        if not has_all_permission_bits(held=operator_permission_bits, requested=requested_allow_bits):
            results.append(
                BatchItemResult(
                    kind="channel", name=channel_def.name, status="failed",
                    detail="You don't hold a permission this channel's overwrites would grant.",
                )
            )
            continue

        try:
            created = await create_guild_channel(
                http, bot_token, guild_id, name=channel_def.name, type=channel_def.type, parent_id=parent_id,
                topic=channel_def.topic, nsfw=channel_def.nsfw, rate_limit_per_user=channel_def.rate_limit_per_user,
                bitrate=channel_def.bitrate, user_limit=channel_def.user_limit, overwrites=overwrites or None,
            )
        except DiscordAPIError as exc:
            results.append(BatchItemResult(kind="channel", name=channel_def.name, status="failed", detail=str(exc)))
            continue

        if channel_def.type == CATEGORY_CHANNEL_TYPE:
            category_id_by_name[channel_def.name] = created.id
        existing_channel_names.add(channel_def.name)
        await audit_log.record(
            guild_id, actor_discord_user_id=actor_discord_user_id, action=AuditAction.CHANNEL_CREATE,
            target_type=AuditTargetType.CHANNEL, target_id=created.id, target_name=created.name,
            summary=f"Created channel {created.name!r} via template {template_label!r}.",
        )
        results.append(BatchItemResult(kind="channel", name=channel_def.name, status="created"))

    return results


# --- Channel canvas: batch create/edit/delete/reorder ---
#
# The canvas is stage-then-apply: every drag, rename, permission change,
# create, and delete only edits a local draft in the browser (see
# app/web/static/channel_canvas.js) - nothing reaches Discord until one
# "Apply" submits the whole draft as a single batch here. A freshly
# created category has no real Discord id yet at the time a channel that
# belongs in it is also being created in the same batch, so both ends of
# that relationship are addressed by a client-chosen `temp_id` string
# instead, resolved to a real id once the category is actually created
# (below, in creation order) - the same problem apply_template's
# category-before-children ordering solves, just needing an extra layer of
# indirection here since the *ids* don't exist yet either, not just the
# ordering.


class CanvasBatchValidationError(Exception):
    """Raised for a malformed canvas batch payload. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class ChannelRef:
    kind: str  # "existing" or "temp"
    value: int | str  # a real channel id for "existing", a client temp_id for "temp"


@dataclass(frozen=True, slots=True)
class ChannelBatchOverwrite:
    role_id: int
    allow: int = 0
    deny: int = 0


@dataclass(frozen=True, slots=True)
class ChannelBatchItem:
    op: str  # "create", "edit", or "delete"
    id: int | None = None  # existing channel id - required for "edit"/"delete"
    temp_id: str | None = None  # required for "create"
    type: int | None = None  # required for "create"
    name: str | None = None
    parent: ChannelRef | None = None
    topic: str | None = None
    nsfw: bool = False
    rate_limit_per_user: int = 0
    bitrate: int | None = None
    user_limit: int | None = None
    overwrites: tuple[ChannelBatchOverwrite, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChannelPositionMove:
    ref: ChannelRef
    position: int
    parent: ChannelRef | None = None


def _parse_channel_ref(raw: object, *, field_name: str) -> ChannelRef | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CanvasBatchValidationError(f"{field_name} must be an object.")
    kind = raw.get("kind")
    if kind == "existing":
        try:
            return ChannelRef(kind="existing", value=int(raw["id"]))
        except (KeyError, TypeError, ValueError):
            raise CanvasBatchValidationError(f"{field_name} is missing a valid existing channel id.") from None
    if kind == "temp":
        temp_id = raw.get("id")
        if not isinstance(temp_id, str) or not temp_id:
            raise CanvasBatchValidationError(f"{field_name} is missing a valid temp id.")
        return ChannelRef(kind="temp", value=temp_id)
    raise CanvasBatchValidationError(f"{field_name} must have kind 'existing' or 'temp'.")


def _parse_overwrites(raw: object) -> tuple[ChannelBatchOverwrite, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CanvasBatchValidationError("A channel's overwrites must be a list.")
    overwrites = []
    for entry in raw:
        try:
            overwrites.append(
                ChannelBatchOverwrite(
                    role_id=int(entry["role_id"]), allow=int(entry.get("allow", 0)), deny=int(entry.get("deny", 0))
                )
            )
        except (KeyError, TypeError, ValueError):
            raise CanvasBatchValidationError("A channel overwrite is malformed.") from None
    return tuple(overwrites)


def parse_channel_canvas_batch(data: object) -> tuple[list[ChannelBatchItem], list[ChannelPositionMove]]:
    """Validate and parse the JSON body channel_canvas.js POSTs on Apply
    into the structured types apply_channel_canvas_batch expects. Kept
    separate from that function so malformed-payload rejection (a 400, not
    a 500) can happen before any Discord call is made."""
    if not isinstance(data, dict):
        raise CanvasBatchValidationError("Malformed batch payload.")

    items: list[ChannelBatchItem] = []
    for raw in data.get("channels") or []:
        if not isinstance(raw, dict):
            raise CanvasBatchValidationError("Each channel item must be an object.")
        op = raw.get("op")
        if op not in ("create", "edit", "delete"):
            raise CanvasBatchValidationError("Each channel item needs op 'create', 'edit', or 'delete'.")

        channel_id = None
        if op in ("edit", "delete"):
            try:
                channel_id = int(raw["id"])
            except (KeyError, TypeError, ValueError):
                raise CanvasBatchValidationError(f"An {op} item is missing a valid id.") from None

        temp_id = raw.get("temp_id")
        if op == "create" and (not isinstance(temp_id, str) or not temp_id):
            raise CanvasBatchValidationError("A create item is missing a temp_id.")

        name = raw.get("name")
        if op != "delete" and (not isinstance(name, str) or not name.strip()):
            raise CanvasBatchValidationError("A channel needs a name.")

        channel_type = raw.get("type")
        if op == "create" and channel_type not in _CREATABLE_CHANNEL_TYPES:
            raise CanvasBatchValidationError("A new channel needs a valid type.")

        bitrate = raw.get("bitrate")
        user_limit = raw.get("user_limit")

        items.append(
            ChannelBatchItem(
                op=op,
                id=channel_id,
                temp_id=temp_id if isinstance(temp_id, str) else None,
                type=channel_type if isinstance(channel_type, int) else None,
                name=name.strip() if isinstance(name, str) else None,
                parent=_parse_channel_ref(raw.get("parent"), field_name="A channel's parent"),
                topic=(raw.get("topic") or None),
                nsfw=bool(raw.get("nsfw", False)),
                rate_limit_per_user=int(raw.get("rate_limit_per_user") or 0),
                bitrate=bitrate if isinstance(bitrate, int) else None,
                user_limit=user_limit if isinstance(user_limit, int) else None,
                overwrites=_parse_overwrites(raw.get("overwrites")),
            )
        )

    moves: list[ChannelPositionMove] = []
    for raw in data.get("positions") or []:
        if not isinstance(raw, dict):
            raise CanvasBatchValidationError("Each position entry must be an object.")
        ref = _parse_channel_ref(raw.get("ref"), field_name="A position entry's ref")
        if ref is None:
            raise CanvasBatchValidationError("A position entry needs a channel reference.")
        try:
            position = int(raw["position"])
        except (KeyError, TypeError, ValueError):
            raise CanvasBatchValidationError("A position entry needs a valid position.") from None
        moves.append(
            ChannelPositionMove(ref=ref, position=position, parent=_parse_channel_ref(raw.get("parent"), field_name="A position entry's parent"))
        )

    return items, moves


async def apply_channel_canvas_batch(
    http: httpx.AsyncClient,
    bot_token: str,
    guild_id: int,
    *,
    items: list[ChannelBatchItem],
    moves: list[ChannelPositionMove],
    actor_discord_user_id: int,
    operator_permission_bits: int,
    current_channels: list[DiscordChannelDetail],
) -> list[BatchItemResult]:
    """Apply a channel canvas's staged draft: creates, then edits, then
    deletes, then one bulk reorder/reparent call for whatever survived -
    each phase sequential and per-item, exactly like apply_template, so a
    failure on one item never blocks the rest and every create/edit still
    goes through the same operator-permission check a single manual action
    would.
    """
    results: list[BatchItemResult] = []
    audit_log = AuditLogService()

    current_by_id = {c.id: c for c in current_channels}
    existing_channel_ids = set(current_by_id)
    temp_id_to_real_id: dict[str, int] = {}

    def _resolve_parent(ref: ChannelRef | None) -> int | None:
        if ref is None:
            return None
        if ref.kind == "existing":
            return ref.value
        return temp_id_to_real_id.get(ref.value)

    def _permission_check(overwrites: tuple[ChannelBatchOverwrite, ...]) -> bool:
        requested_allow_bits = 0
        for o in overwrites:
            requested_allow_bits |= o.allow
        return has_all_permission_bits(held=operator_permission_bits, requested=requested_allow_bits)

    # Phase 1: creates - categories first, so a channel created later in
    # this same batch can reference one as its parent.
    creates = [i for i in items if i.op == "create"]
    ordered_creates = [i for i in creates if i.type == CATEGORY_CHANNEL_TYPE]
    ordered_creates += [i for i in creates if i.type != CATEGORY_CHANNEL_TYPE]

    for item in ordered_creates:
        display_name = item.name or "(unnamed)"
        if not _permission_check(item.overwrites):
            results.append(
                BatchItemResult(
                    kind="channel", name=display_name, status="failed",
                    detail="You don't hold a permission this channel's overwrites would grant.",
                )
            )
            continue

        overwrites = [DiscordPermissionOverwrite(role_id=o.role_id, allow=o.allow, deny=o.deny) for o in item.overwrites]
        try:
            created = await create_guild_channel(
                http, bot_token, guild_id, name=item.name, type=item.type, parent_id=_resolve_parent(item.parent),
                topic=item.topic, nsfw=item.nsfw, rate_limit_per_user=item.rate_limit_per_user,
                bitrate=item.bitrate, user_limit=item.user_limit, overwrites=overwrites or None,
            )
        except DiscordAPIError as exc:
            results.append(BatchItemResult(kind="channel", name=display_name, status="failed", detail=str(exc)))
            continue

        if item.temp_id:
            temp_id_to_real_id[item.temp_id] = created.id
        await audit_log.record(
            guild_id, actor_discord_user_id=actor_discord_user_id, action=AuditAction.CHANNEL_CREATE,
            target_type=AuditTargetType.CHANNEL, target_id=created.id, target_name=created.name,
            summary=f"Created channel {created.name!r} via the channel canvas.",
        )
        results.append(BatchItemResult(kind="channel", name=created.name, status="created"))

    # Phase 2: edits - basic fields via a normal PATCH, then overwrites
    # diffed one role at a time (only the roles explicitly present in this
    # edit's overwrite list are touched), mirroring the existing single-
    # channel edit form's semantics exactly rather than replacing the
    # whole overwrite set at once.
    for item in items:
        if item.op != "edit" or item.id is None:
            continue
        existing = current_by_id.get(item.id)
        if existing is None:
            results.append(
                BatchItemResult(kind="channel", name=item.name or str(item.id), status="failed", detail="This channel no longer exists.")
            )
            continue

        if not _permission_check(item.overwrites):
            results.append(
                BatchItemResult(
                    kind="channel", name=item.name or existing.name, status="failed",
                    detail="You don't hold a permission this channel's overwrites would grant.",
                )
            )
            continue

        try:
            updated = await edit_guild_channel(
                http, bot_token, item.id, name=item.name or existing.name, parent_id=_resolve_parent(item.parent),
                topic=item.topic, nsfw=item.nsfw, rate_limit_per_user=item.rate_limit_per_user,
                bitrate=item.bitrate, user_limit=item.user_limit,
            )
        except DiscordAPIError as exc:
            results.append(BatchItemResult(kind="channel", name=item.name or existing.name, status="failed", detail=str(exc)))
            continue

        requested_by_role = {o.role_id: o for o in item.overwrites}
        existing_role_ids = {o.role_id for o in existing.overwrites}
        try:
            for role_id, o in requested_by_role.items():
                await put_channel_permission_overwrite(http, bot_token, item.id, role_id=role_id, allow=o.allow, deny=o.deny)
            for role_id in existing_role_ids - set(requested_by_role):
                await delete_channel_permission_overwrite(http, bot_token, item.id, role_id)
        except DiscordAPIError as exc:
            results.append(
                BatchItemResult(kind="channel", name=updated.name, status="failed", detail=f"Updated, but an overwrite change failed ({exc}).")
            )
            continue

        await audit_log.record(
            guild_id, actor_discord_user_id=actor_discord_user_id, action=AuditAction.CHANNEL_EDIT,
            target_type=AuditTargetType.CHANNEL, target_id=updated.id, target_name=updated.name,
            summary=f"Edited channel {updated.name!r} via the channel canvas.",
        )
        results.append(BatchItemResult(kind="channel", name=updated.name, status="edited"))

    # Phase 3: deletes
    deleted_ids: set[int] = set()
    for item in items:
        if item.op != "delete" or item.id is None:
            continue
        existing = current_by_id.get(item.id)
        name = existing.name if existing else str(item.id)
        try:
            await delete_guild_channel(http, bot_token, item.id)
        except DiscordAPIError as exc:
            results.append(BatchItemResult(kind="channel", name=name, status="failed", detail=str(exc)))
            continue
        deleted_ids.add(item.id)
        await audit_log.record(
            guild_id, actor_discord_user_id=actor_discord_user_id, action=AuditAction.CHANNEL_DELETE,
            target_type=AuditTargetType.CHANNEL, target_id=item.id, target_name=name,
            summary=f"Deleted channel {name!r} via the channel canvas.",
        )
        results.append(BatchItemResult(kind="channel", name=name, status="deleted"))

    # Phase 4: one bulk reorder/reparent call for whatever survived. A
    # move referencing a temp id whose create failed, or an existing id
    # that was just deleted (or didn't exist to begin with), is silently
    # dropped - there's nothing left to position.
    resolved_moves: list[tuple[int, int, int | None]] = []
    for move in moves:
        if move.ref.kind == "existing":
            channel_id = move.ref.value
            if channel_id in deleted_ids or channel_id not in existing_channel_ids:
                continue
        else:
            resolved = temp_id_to_real_id.get(move.ref.value)
            if resolved is None:
                continue
            channel_id = resolved
        resolved_moves.append((channel_id, move.position, _resolve_parent(move.parent)))

    if resolved_moves:
        try:
            await bulk_edit_channel_positions(http, bot_token, guild_id, resolved_moves)
        except DiscordAPIError as exc:
            results.append(BatchItemResult(kind="channel", name="(reorder)", status="failed", detail=str(exc)))
        else:
            results.append(
                BatchItemResult(kind="channel", name="(reorder)", status="moved", detail=f"Repositioned {len(resolved_moves)} channel(s).")
            )

    return results
