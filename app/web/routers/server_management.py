"""Server management: full lifecycle for a guild's actual Discord roles and
channels, plus reusable templates and an audit log.

This is the highest-blast-radius feature on this dashboard - it creates and
destroys real guild structure, unlike every prior feature (which only posts
messages or toggles configuration). No discord.py-touching "service" wraps
this - the orchestration (validate -> call Discord -> audit
log) lives directly here, the same way app/web/routers/roles.py already
does for the existing self-assignable-roles feature. `app/web/
server_management.py` holds the pieces meaty enough to unit-test without
FastAPI (template snapshot/apply).

Uses discord.Permissions directly for flag-name <-> bitfield conversion,
matching the precedent already set by app/web/routers/custom_commands.py
and app/web/role_components.py - the two other places app/web/ imports
discord.py for pure bit/flag utility, distinct from app/services/ which
must stay discord.py-free.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import discord
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.db.models.audit_log_entry import AuditAction, AuditTargetType
from app.services.audit_log_service import AuditLogService
from app.services.event_log_service import EventLogService
from app.services.template_service import TemplateService, TemplateValidationError
from app.utils.permissions import has_all_permission_bits
from app.utils.templates import format_uptime
from app.web.bot_client import BotStatusView
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.discord_client import (
    CATEGORY_CHANNEL_TYPE,
    DiscordAPIError,
    DiscordChannelDetail,
    DiscordGuildCounts,
    DiscordRoleDetail,
    bot_top_role_position,
    bulk_edit_role_positions,
    create_guild_role,
    delete_guild_role,
    edit_guild_role,
    fetch_bot_role_ids,
    fetch_guild_channels_detailed,
    fetch_guild_counts,
    fetch_guild_roles_detailed,
)
from app.web.guild_options import guild_page_context
from app.web.permission_groups import CHANNEL_OVERWRITE_PERMISSIONS, PERMISSION_GROUPS
from app.web.routers.console import load_bot_status
from app.web.server_management import (
    CanvasBatchValidationError,
    RoleReorderValidationError,
    apply_channel_canvas_batch,
    apply_template,
    build_definition_from_live_state,
    parse_channel_canvas_batch,
    resolve_role_reorder,
)
from app.web.sessions import LoadedSession

router = APIRouter()

_AUDIT_LOG_PER_PAGE = 50


def _permission_names(bits: int) -> set[str]:
    """The subset of PERMISSION_GROUPS' canonical flag names that `bits` grants."""
    perms = discord.Permissions(bits)
    names = set()
    for flags in PERMISSION_GROUPS.values():
        for name in flags:
            if getattr(perms, name):
                names.add(name)
    return names


def _permission_bits(names: set[str]) -> int:
    return discord.Permissions(**{name: True for name in names}).value


async def _load_role_state(request: Request, guild_id: int) -> tuple[list[DiscordRoleDetail], set[int]]:
    """Live roles (excluding @everyone) plus the set of role ids C3P0 can
    actually edit/delete - not managed, and below the bot's own top role,
    mirroring guild_options.load_guild_discord_state's assignable_roles
    computation. Degrades to empty on a Discord API failure, same as that
    function, so a submitted id always fails a fresh resolve_optional_id-
    style check rather than acting on stale trust.
    """
    config = request.app.state.web_config
    http = request.app.state.http_client
    try:
        roles, bot_role_ids = await asyncio.gather(
            fetch_guild_roles_detailed(http, config.discord_bot_token, guild_id),
            fetch_bot_role_ids(http, config.discord_bot_token, guild_id, config.discord_client_id),
        )
    except DiscordAPIError:
        return [], set()

    roles = [r for r in roles if r.id != guild_id]  # exclude @everyone
    top_position = bot_top_role_position(roles, bot_role_ids)
    editable_ids = {r.id for r in roles if not r.managed and r.position < top_position}
    return roles, editable_ids


@router.get("/guilds/{guild_id}/server-management/roles")
async def list_roles(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    roles, editable_role_ids = await _load_role_state(request, guild_id)
    roles.sort(key=lambda r: r.position, reverse=True)
    fixed_roles = [r for r in roles if r.id not in editable_role_ids]
    editable_roles = [r for r in roles if r.id in editable_role_ids]

    context = await guild_page_context(guild_id, session, "server-management")
    context.update(
        {
            "active_subtab": "roles",
            "fixed_roles": fixed_roles,
            "editable_roles": editable_roles,
            # Only id/name cross into JS - the canvas only ever reorders,
            # it never needs a role's permissions/color/etc.
            "editable_roles_json": [{"id": str(r.id), "name": r.name} for r in editable_roles],
        }
    )
    return request.app.state.templates.TemplateResponse(request, "server_management_roles_list.html", context)


@router.get("/guilds/{guild_id}/server-management/roles/new")
async def new_role_form(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    context = await guild_page_context(guild_id, session, "server-management")
    context.update(
        {
            "active_subtab": "roles",
            "role": None,
            "action_url": f"/guilds/{guild_id}/server-management/roles",
            "permission_groups": PERMISSION_GROUPS,
            "checked_permissions": set(),
            "operator_permissions": _permission_names(session.guild_permissions.get(guild_id, 0)),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "server_management_role_form.html", context)


_ALL_PERMISSION_NAMES = {name for flags in PERMISSION_GROUPS.values() for name in flags}


def _checked_permission_names(form) -> set[str]:
    return {name for name in form.getlist("permissions") if name in _ALL_PERMISSION_NAMES}


@router.post("/guilds/{guild_id}/server-management/roles")
async def create_role(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    try:
        color = int(form.get("color") or 0)
    except ValueError:
        color = 0
    hoist = "hoist" in form
    mentionable = "mentionable" in form
    checked = _checked_permission_names(form)
    operator_names = _permission_names(session.guild_permissions.get(guild_id, 0))

    async def _rerender(error: str) -> Response:
        context = await guild_page_context(guild_id, session, "server-management")
        context.update(
            {
                "active_subtab": "roles",
                "role": None,
                "action_url": f"/guilds/{guild_id}/server-management/roles",
                "permission_groups": PERMISSION_GROUPS,
                "checked_permissions": checked,
                "operator_permissions": operator_names,
                "error": error,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "server_management_role_form.html", context, status_code=400
        )

    if not name:
        return await _rerender("Name cannot be empty.")
    if len(name) > 100:
        return await _rerender("Name must be 100 characters or fewer.")

    requested_bits = _permission_bits(checked)
    # Create has no prior state - every requested bit is being newly
    # granted, so this checks the full requested set.
    if not has_all_permission_bits(held=session.guild_permissions.get(guild_id, 0), requested=requested_bits):
        return await _rerender("You can't grant a permission you don't hold yourself.")

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    try:
        role = await create_guild_role(
            http, bot_token, guild_id, name=name, color=color, hoist=hoist,
            mentionable=mentionable, permissions=requested_bits,
        )
    except DiscordAPIError as exc:
        return await _rerender(f"Discord rejected that role ({exc}).")

    await AuditLogService().record(
        guild_id,
        actor_discord_user_id=session.discord_user_id,
        action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE,
        target_id=role.id,
        target_name=role.name,
        summary=f"Created role {role.name!r}.",
    )
    return RedirectResponse(f"/guilds/{guild_id}/server-management/roles", status_code=303)


# Registered before /roles/{role_id} below - Starlette matches routes in
# registration order, and "/roles/reorder" would otherwise bind "reorder"
# to that route's role_id: int path param and 422 on the type coercion
# before ever reaching this one.
@router.post("/guilds/{guild_id}/server-management/roles/reorder")
async def reorder_roles(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    # The batch travels as a form field, same reason as the channel
    # canvas's apply route: require_csrf reads its token via
    # request.form(), so the reorder payload rides alongside it as a JSON
    # string in an "order" field rather than a raw application/json body.
    form = await request.form()
    try:
        raw_order = json.loads(str(form.get("order") or "[]"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed reorder payload.") from None
    if not isinstance(raw_order, list):
        raise HTTPException(status_code=400, detail="Malformed reorder payload.")
    try:
        order = [int(v) for v in raw_order]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed reorder payload.") from None

    roles, editable_role_ids = await _load_role_state(request, guild_id)
    try:
        moves = resolve_role_reorder(order, current_roles=roles, editable_role_ids=editable_role_ids)
    except RoleReorderValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    try:
        await bulk_edit_role_positions(http, bot_token, guild_id, moves)
    except DiscordAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Discord rejected the reorder ({exc}).") from exc

    return RedirectResponse(f"/guilds/{guild_id}/server-management/roles", status_code=303)


async def _resolve_editable_role(request: Request, guild_id: int, role_id: int) -> DiscordRoleDetail | None:
    roles, editable_role_ids = await _load_role_state(request, guild_id)
    if role_id not in editable_role_ids:
        return None
    return next((r for r in roles if r.id == role_id), None)


@router.get("/guilds/{guild_id}/server-management/roles/{role_id}/edit")
async def edit_role_form(
    request: Request,
    guild_id: int,
    role_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    role = await _resolve_editable_role(request, guild_id, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="That role isn't editable - it may be managed, above C3P0's own role, or no longer exist.")

    context = await guild_page_context(guild_id, session, "server-management")
    context.update(
        {
            "active_subtab": "roles",
            "role": role,
            "action_url": f"/guilds/{guild_id}/server-management/roles/{role_id}",
            "permission_groups": PERMISSION_GROUPS,
            "checked_permissions": _permission_names(role.permissions),
            "operator_permissions": _permission_names(session.guild_permissions.get(guild_id, 0)),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "server_management_role_form.html", context)


@router.post("/guilds/{guild_id}/server-management/roles/{role_id}")
async def update_role(
    request: Request,
    guild_id: int,
    role_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    role = await _resolve_editable_role(request, guild_id, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="That role isn't editable - it may be managed, above C3P0's own role, or no longer exist.")

    form = await request.form()
    name = str(form.get("name") or "").strip()
    try:
        color = int(form.get("color") or 0)
    except ValueError:
        color = 0
    hoist = "hoist" in form
    mentionable = "mentionable" in form
    checked = _checked_permission_names(form)
    operator_names = _permission_names(session.guild_permissions.get(guild_id, 0))

    async def _rerender(error: str) -> Response:
        context = await guild_page_context(guild_id, session, "server-management")
        context.update(
            {
                "active_subtab": "roles",
                "role": role,
                "action_url": f"/guilds/{guild_id}/server-management/roles/{role_id}",
                "permission_groups": PERMISSION_GROUPS,
                "checked_permissions": checked,
                "operator_permissions": operator_names,
                "error": error,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "server_management_role_form.html", context, status_code=400
        )

    if not name:
        return await _rerender("Name cannot be empty.")
    if len(name) > 100:
        return await _rerender("Name must be 100 characters or fewer.")

    requested_bits = _permission_bits(checked)
    # Only bits being NEWLY granted (not already on this role) need the
    # operator to hold them - a bit the role already had (perhaps granted
    # earlier by someone with broader access) is left alone by an unrelated
    # edit (a name/color change) rather than being silently stripped just
    # because the current editor doesn't happen to hold it too.
    newly_granted = requested_bits & ~role.permissions
    if not has_all_permission_bits(held=session.guild_permissions.get(guild_id, 0), requested=newly_granted):
        return await _rerender("You can't grant a permission you don't hold yourself.")

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    try:
        updated = await edit_guild_role(
            http, bot_token, guild_id, role_id, name=name, color=color, hoist=hoist,
            mentionable=mentionable, permissions=requested_bits,
        )
    except DiscordAPIError as exc:
        return await _rerender(f"Discord rejected that update ({exc}).")

    await AuditLogService().record(
        guild_id,
        actor_discord_user_id=session.discord_user_id,
        action=AuditAction.ROLE_EDIT,
        target_type=AuditTargetType.ROLE,
        target_id=updated.id,
        target_name=updated.name,
        summary=f"Edited role {updated.name!r}.",
    )
    return RedirectResponse(f"/guilds/{guild_id}/server-management/roles", status_code=303)


@router.post("/guilds/{guild_id}/server-management/roles/{role_id}/delete")
async def delete_role(
    request: Request,
    guild_id: int,
    role_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    role = await _resolve_editable_role(request, guild_id, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="That role isn't deletable - it may be managed, above C3P0's own role, or no longer exist.")

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    try:
        await delete_guild_role(http, bot_token, guild_id, role_id)
    except DiscordAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Discord rejected the delete ({exc}).") from exc

    await AuditLogService().record(
        guild_id,
        actor_discord_user_id=session.discord_user_id,
        action=AuditAction.ROLE_DELETE,
        target_type=AuditTargetType.ROLE,
        target_id=role.id,
        target_name=role.name,
        summary=f"Deleted role {role.name!r}.",
    )
    return RedirectResponse(f"/guilds/{guild_id}/server-management/roles", status_code=303)


# --- Channel canvas ---
# Retires the old form-based Channels list/create/edit pages entirely - the
# canvas (app/web/static/channel_canvas.js) is now the only surface for
# channel management. It's stage-then-apply: every drag, rename,
# permission change, create, and delete only edits a local draft in the
# browser; nothing reaches Discord until one "Apply" POSTs the whole draft
# as a batch to apply_channel_canvas_batch (app/web/server_management.py),
# which re-validates and re-fetches live state itself rather than trusting
# anything about the submitted draft's idea of "current" - same "never
# trust a submitted id" principle every other page on this dashboard
# already follows. No bot-hierarchy check is needed for channels the way
# roles need one - Discord channels have no position-based hierarchy
# concept; the bot either holds MANAGE_CHANNELS or Discord's own REST call
# 403s, surfaced as a normal per-item "failed" result in the batch.


async def _load_channel_state(
    request: Request, guild_id: int
) -> tuple[list[DiscordChannelDetail], list[DiscordRoleDetail]]:
    config = request.app.state.web_config
    http = request.app.state.http_client
    try:
        channels, roles = await asyncio.gather(
            fetch_guild_channels_detailed(http, config.discord_bot_token, guild_id),
            fetch_guild_roles_detailed(http, config.discord_bot_token, guild_id),
        )
    except DiscordAPIError:
        return [], []
    return channels, roles


def _channel_to_canvas_dict(channel: DiscordChannelDetail) -> dict:
    # Every Discord snowflake below is stringified before it crosses into
    # JSON embedded in the page - a snowflake regularly exceeds 2**53, so a
    # raw JSON *number* silently loses precision the instant JS parses it
    # (Number is a float64), and two distinct channels created close
    # together in time (differing only in the low bits Discord assigns)
    # can round to the exact same value. That's what caused channels to
    # vanish/duplicate in the canvas - ids must stay strings end-to-end on
    # the JS side; parse_channel_canvas_batch's int(...) calls already
    # accept a numeric string back just as happily as a number.
    return {
        "id": str(channel.id),
        "name": channel.name,
        "type": channel.type,
        "position": channel.position,
        "parent_id": str(channel.parent_id) if channel.parent_id is not None else None,
        "topic": channel.topic,
        "nsfw": channel.nsfw,
        "rate_limit_per_user": channel.rate_limit_per_user,
        "bitrate": channel.bitrate,
        "user_limit": channel.user_limit,
        "overwrites": [{"role_id": str(o.role_id), "allow": o.allow, "deny": o.deny} for o in channel.overwrites],
    }


def _role_to_canvas_dict(role: DiscordRoleDetail) -> dict:
    return {"id": str(role.id), "name": role.name, "managed": role.managed}


async def _load_guild_counts(request: Request, guild_id: int) -> DiscordGuildCounts | None:
    config = request.app.state.web_config
    try:
        return await fetch_guild_counts(request.app.state.http_client, config.discord_bot_token, guild_id)
    except (DiscordAPIError, httpx.HTTPError):
        return None


def _server_stat_tiles(
    channels: list[DiscordChannelDetail],
    counts: DiscordGuildCounts | None,
    status: BotStatusView | None,
    net_joins: int,
) -> list[dict]:
    """MEMBERS / ONLINE / CHANNELS / UPTIME tiles on the Server page. Any
    value that couldn't be fetched renders as "--" rather than a fake 0."""
    categories = sum(1 for c in channels if c.type == CATEGORY_CHANNEL_TYPE)
    tiles = [
        {
            "label": "MEMBERS",
            "value": f"{counts.member_count:,}" if counts else "--",
            "sub": f"NET {net_joins:+d} THIS WEEK",
        },
        {
            "label": "ONLINE",
            "value": f"{counts.presence_count:,}" if counts else "--",
            "sub": f"{round(100 * counts.presence_count / counts.member_count)}% ACTIVE"
            if counts and counts.member_count
            else "--",
        },
        {
            "label": "CHANNELS",
            "value": f"{len(channels) - categories:02d}" if channels else "--",
            "sub": f"{categories} CATEGOR{'Y' if categories == 1 else 'IES'}",
        },
    ]
    if status is not None and status.uptime_seconds is not None:
        restarted = datetime.fromisoformat(status.started_at).strftime("%m/%d") if status.started_at else "--"
        tiles.append(
            {
                "label": "UPTIME",
                "value": format_uptime(status.uptime_seconds).replace(" ", ""),
                "sub": f"LAST RESTART {restarted}",
            }
        )
    else:
        tiles.append({"label": "UPTIME", "value": "--", "sub": "BOT UNREACHABLE"})
    return tiles


@router.get("/guilds/{guild_id}/server-management/channels")
async def channel_canvas_view(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    (channels, roles), counts, status, net_joins = await asyncio.gather(
        _load_channel_state(request, guild_id),
        _load_guild_counts(request, guild_id),
        load_bot_status(request),
        EventLogService().net_joins_since(guild_id),
    )

    context = await guild_page_context(guild_id, session, "server-management")
    context.update(
        {
            "active_subtab": "channels",
            "channels_json": [_channel_to_canvas_dict(c) for c in channels],
            "roles_json": [_role_to_canvas_dict(r) for r in roles if r.id != guild_id],
            "everyone_role_id": guild_id,
            "overwrite_permissions": CHANNEL_OVERWRITE_PERMISSIONS,
            "permission_bit_values": {flag: _permission_bits({flag}) for flag in CHANNEL_OVERWRITE_PERMISSIONS},
            "operator_permissions": sorted(_permission_names(session.guild_permissions.get(guild_id, 0))),
            "apply_url": f"/guilds/{guild_id}/server-management/channels/canvas/apply",
            "stat_tiles": _server_stat_tiles(channels, counts, status, net_joins),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "server_management_channel_canvas.html", context)


@router.post("/guilds/{guild_id}/server-management/channels/canvas/apply")
async def apply_channel_canvas(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    # The batch travels as a form field (not a raw `application/json` body)
    # because require_csrf reads the csrf token via request.form() - same
    # form, same field name, as every other mutating route on this
    # dashboard, just with a "batch" field carrying a JSON string alongside
    # csrf_token instead of a handful of individual form fields.
    form = await request.form()
    try:
        raw_batch = json.loads(str(form.get("batch") or "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed batch payload.") from None

    try:
        items, moves = parse_channel_canvas_batch(raw_batch)
    except CanvasBatchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    channels, _roles = await _load_channel_state(request, guild_id)
    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    results = await apply_channel_canvas_batch(
        http, bot_token, guild_id, items=items, moves=moves,
        actor_discord_user_id=session.discord_user_id,
        operator_permission_bits=session.guild_permissions.get(guild_id, 0),
        current_channels=channels,
    )
    return await _render_apply_result(
        request, guild_id, session, template_name="Channel Canvas", results=results, active_subtab="channels"
    )


# --- Templates ---


@router.get("/guilds/{guild_id}/server-management/templates")
async def list_templates(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    catalog = await TemplateService().list_catalog(guild_id)

    context = await guild_page_context(guild_id, session, "server-management")
    context.update({"active_subtab": "templates", "catalog": catalog})
    return request.app.state.templates.TemplateResponse(request, "server_management_templates.html", context)


def _group_template_channels(definition) -> list[dict]:
    """Same grouping shape as _group_channels_by_category, but over a
    TemplateDefinition's name-keyed TemplateChannelDefs instead of live,
    id-keyed DiscordChannelDetail - a template has no ids yet."""
    categories = [c for c in definition.channels if c.type == CATEGORY_CHANNEL_TYPE]
    groups = []
    uncategorized = [c for c in definition.channels if c.type != CATEGORY_CHANNEL_TYPE and c.parent_name is None]
    if uncategorized:
        groups.append({"category": None, "channels": uncategorized})
    for category in categories:
        children = [c for c in definition.channels if c.parent_name == category.name]
        groups.append({"category": category, "channels": children})
    return groups


async def _render_template_preview(request: Request, guild_id: int, session: LoadedSession, template) -> Response:
    role_rows = [
        {
            "name": r.name,
            "hoist": r.hoist,
            "mentionable": r.mentionable,
            "permissions": sorted(_permission_names(r.permissions)),
        }
        for r in template.definition.roles
    ]
    context = await guild_page_context(guild_id, session, "server-management")
    context.update(
        {
            "active_subtab": "templates",
            "template": template,
            "role_rows": role_rows,
            "groups": _group_template_channels(template.definition),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "server_management_template_preview.html", context)


@router.get("/guilds/{guild_id}/server-management/templates/builtin/{slug}/preview")
async def preview_builtin_template(
    request: Request,
    guild_id: int,
    slug: str,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    template = TemplateService().get_builtin(slug)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    return await _render_template_preview(request, guild_id, session, template)


@router.get("/guilds/{guild_id}/server-management/templates/{template_id}/preview")
async def preview_saved_template(
    request: Request,
    guild_id: int,
    template_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    template = await TemplateService().get_owned(guild_id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    return await _render_template_preview(request, guild_id, session, template)


@router.post("/guilds/{guild_id}/server-management/templates")
async def save_current_layout_as_template(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    description = str(form.get("description") or "").strip() or None

    async def _rerender(error: str) -> Response:
        catalog = await TemplateService().list_catalog(guild_id)
        context = await guild_page_context(guild_id, session, "server-management")
        context.update({"active_subtab": "templates", "catalog": catalog, "error": error})
        return request.app.state.templates.TemplateResponse(
            request, "server_management_templates.html", context, status_code=400
        )

    channels, roles = await _load_channel_state(request, guild_id)
    if not channels and not roles:
        return await _rerender("Couldn't reach Discord to snapshot this server's current layout.")

    definition = build_definition_from_live_state(guild_id, roles, channels)

    try:
        await TemplateService().save_as_template(
            guild_id, name=name, description=description, definition=definition,
            created_by=session.discord_user_id,
        )
    except TemplateValidationError as exc:
        return await _rerender(str(exc))

    return RedirectResponse(f"/guilds/{guild_id}/server-management/templates", status_code=303)


@router.post("/guilds/{guild_id}/server-management/templates/{template_id}/delete")
async def delete_template(
    guild_id: int,
    template_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    deleted = await TemplateService().delete(guild_id, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found.")
    return RedirectResponse(f"/guilds/{guild_id}/server-management/templates", status_code=303)


async def _render_apply_result(
    request: Request, guild_id: int, session: LoadedSession, *, template_name: str, results,
    active_subtab: str = "templates",
) -> Response:
    context = await guild_page_context(guild_id, session, "server-management")
    context.update({"active_subtab": active_subtab, "template_name": template_name, "results": results})
    return request.app.state.templates.TemplateResponse(
        request, "server_management_template_apply_result.html", context
    )


@router.post("/guilds/{guild_id}/server-management/templates/builtin/{slug}/apply")
async def apply_builtin_template(
    request: Request,
    guild_id: int,
    slug: str,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    template = TemplateService().get_builtin(slug)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    channels, roles = await _load_channel_state(request, guild_id)
    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    results = await apply_template(
        http, bot_token, guild_id, definition=template.definition, template_label=template.name,
        actor_discord_user_id=session.discord_user_id,
        operator_permission_bits=session.guild_permissions.get(guild_id, 0),
        current_roles=roles, current_channels=channels,
    )
    return await _render_apply_result(request, guild_id, session, template_name=template.name, results=results)


@router.post("/guilds/{guild_id}/server-management/templates/{template_id}/apply")
async def apply_saved_template(
    request: Request,
    guild_id: int,
    template_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    template = await TemplateService().get_owned(guild_id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    channels, roles = await _load_channel_state(request, guild_id)
    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    results = await apply_template(
        http, bot_token, guild_id, definition=template.definition, template_label=template.name,
        actor_discord_user_id=session.discord_user_id,
        operator_permission_bits=session.guild_permissions.get(guild_id, 0),
        current_roles=roles, current_channels=channels,
    )
    return await _render_apply_result(request, guild_id, session, template_name=template.name, results=results)


# --- Audit log ---


@router.get("/guilds/{guild_id}/server-management/audit-log")
async def show_audit_log(
    request: Request,
    guild_id: int,
    offset: int = 0,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    audit_log = AuditLogService()
    entries = await audit_log.list_for_guild(guild_id, limit=_AUDIT_LOG_PER_PAGE, offset=offset)

    context = await guild_page_context(guild_id, session, "server-management")
    context.update(
        {
            "active_subtab": "audit-log",
            "entries": entries,
            "offset": offset,
            "next_offset": offset + _AUDIT_LOG_PER_PAGE,
            "has_more": len(entries) == _AUDIT_LOG_PER_PAGE,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "server_management_audit_log.html", context)
