"""Moderation: escalation config, moderation log channel, read-only infraction log.

No UI for filtered_words/link_filter_*/spam_filter_*/caps_filter_* - confirmed
zero reads anywhere in app/cogs/moderation.py, exposing them would imply
working filtering that doesn't exist. ModerationConfig.enabled (a separate
field from escalation_enabled) is in the same boat: never read anywhere
either, so it's left off too rather than exposing a second dead toggle.

The infraction log is read-only for this pass - no resolve action from here
even though ModerationService.resolve_infraction exists, per the approved
plan's scope. Pagination is a plain limit/offset "Load older" link, not full
paging controls.
"""

from __future__ import annotations

import asyncio
import math
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.db.models.infraction import InfractionType
from app.services.config_service import ConfigurationService
from app.services.moderation_service import ModerationService, ModerationValidationError
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.discord_client import DiscordAPIError, fetch_guild_member
from app.web.guild_options import guild_page_context, load_guild_discord_state, resolve_optional_id
from app.web.sessions import LoadedSession

router = APIRouter()

_INFRACTIONS_PER_PAGE = 50
_ESCALATION_ACTIONS = (InfractionType.TIMEOUT, InfractionType.KICK, InfractionType.BAN)
_SORT_COLUMNS = ("created_at", "type", "active")
_STATE_TO_ACTIVE = {"active": True, "resolved": False}
_MEMBER_RESOLUTION_CONCURRENCY = 8


def _parse_infraction_filters(request: Request) -> dict:
    """Query-param filters for the infraction log - kept as raw strings
    here (not yet InfractionType/bool) so they round-trip cleanly back
    into the search form and sort links; _page_context converts them to
    typed values right before calling the service."""
    params = request.query_params
    sort = params.get("sort") or "created_at"
    if sort not in _SORT_COLUMNS:
        sort = "created_at"
    return {
        "q": (params.get("q") or "").strip(),
        "type": params.get("type") or "",
        "state": params.get("state") or "",
        "sort": sort,
        "dir": "asc" if params.get("dir") == "asc" else "desc",
    }


def _filters_as_query(filters: dict, **overrides: str | int) -> str:
    query = {k: v for k, v in filters.items() if v}
    query.update({k: v for k, v in overrides.items() if v})
    return urlencode(query)


def _build_sort_links(guild_id: int, filters: dict) -> dict[str, str]:
    links = {}
    for column in _SORT_COLUMNS:
        next_dir = "asc" if (filters["sort"] == column and filters["dir"] == "desc") else "desc"
        query = _filters_as_query(filters, sort=column, dir=next_dir)
        links[column] = f"/guilds/{guild_id}/moderation?{query}"
    return links


async def _resolve_member_names(request: Request, guild_id: int, infractions: list) -> dict[int, str]:
    """Best-effort id -> display-name map for the user_id/moderator_id
    values on just this page of infractions (never the whole table).
    Falls back to the raw id string per-id on a 404 (the user left/was
    banned - the common case for exactly the infractions logged here) or
    any other Discord error, rather than failing the whole page load."""
    unique_ids = {i.user_id for i in infractions} | {i.moderator_id for i in infractions}
    if not unique_ids:
        return {}

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    semaphore = asyncio.Semaphore(_MEMBER_RESOLUTION_CONCURRENCY)

    async def _resolve_one(user_id: int) -> tuple[int, str]:
        async with semaphore:
            try:
                member = await fetch_guild_member(http, bot_token, guild_id, user_id)
            except DiscordAPIError:
                member = None
        return user_id, member.display_name if member is not None else str(user_id)

    resolved = await asyncio.gather(*(_resolve_one(uid) for uid in unique_ids))
    return dict(resolved)


async def _page_context(guild_id: int, session: LoadedSession, request: Request, *, offset: int) -> dict:
    # ConfigurationService.get_config() and ModerationService.get_config()
    # each independently get-or-create the same shared guild_config row
    # (see both services' _ensure_guild_row) - running them concurrently
    # races two INSERTs for that row. Fetch guild_config first so it
    # unconditionally exists before anything else touches it, then run the
    # remaining three - genuinely independent, get_or_create-free reads -
    # concurrently.
    guild_config = await ConfigurationService().get_config(guild_id)

    filters = _parse_infraction_filters(request)
    try:
        type_filter = InfractionType(filters["type"]) if filters["type"] else None
    except ValueError:
        type_filter = None
    active_filter = _STATE_TO_ACTIVE.get(filters["state"])

    mod_service = ModerationService()
    mod_config, infractions, total_infractions = await asyncio.gather(
        mod_service.get_config(guild_id),
        mod_service.search_infractions_for_guild(
            guild_id, text=filters["q"] or None, type=type_filter, active=active_filter,
            sort=filters["sort"], direction=filters["dir"], limit=_INFRACTIONS_PER_PAGE, offset=offset,
        ),
        mod_service.count_search_infractions_for_guild(
            guild_id, text=filters["q"] or None, type=type_filter, active=active_filter,
        ),
    )
    member_names = await _resolve_member_names(request, guild_id, infractions)

    # dict keys are warning counts as strings (JSON object keys must be
    # strings - see ModerationConfig's own docstring), so a plain dictsort
    # would order "10" before "3". Sort numerically instead.
    sorted_thresholds = sorted(
        ((int(warnings), action) for warnings, action in mod_config.escalation_thresholds.items()),
        key=lambda pair: pair[0],
    )

    # Real page controls (not just a "Load older" link) - a "full log" per
    # the user's own framing needs to say where you are in it, not just
    # offer to go further back.
    total_pages = max(1, math.ceil(total_infractions / _INFRACTIONS_PER_PAGE))
    current_page = (offset // _INFRACTIONS_PER_PAGE) + 1
    prev_offset = max(offset - _INFRACTIONS_PER_PAGE, 0) if offset > 0 else None
    next_offset = offset + _INFRACTIONS_PER_PAGE if offset + _INFRACTIONS_PER_PAGE < total_infractions else None
    pagination = {
        "current_page": current_page,
        "total_pages": total_pages,
        "prev_query": _filters_as_query(filters, offset=prev_offset) if prev_offset is not None else None,
        "next_query": _filters_as_query(filters, offset=next_offset) if next_offset is not None else None,
    }

    context = await guild_page_context(guild_id, session, "moderation")
    context.update(
        {
            "guild_config": guild_config,
            "mod_config": mod_config,
            "sorted_thresholds": sorted_thresholds,
            "total_infractions": total_infractions,
            "infractions": infractions,
            "member_names": member_names,
            "escalation_actions": _ESCALATION_ACTIONS,
            "all_infraction_types": list(InfractionType),
            "filters": filters,
            "sort_links": _build_sort_links(guild_id, filters),
            "offset": offset,
            "pagination": pagination,
        }
    )
    return context


@router.get("/guilds/{guild_id}/moderation")
async def show_moderation(
    request: Request,
    guild_id: int,
    offset: int = 0,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    state, context = await asyncio.gather(
        load_guild_discord_state(request, guild_id),
        _page_context(guild_id, session, request, offset=max(offset, 0)),
    )
    context["text_channels"] = state.text_channels
    return request.app.state.templates.TemplateResponse(request, "moderation.html", context)


@router.post("/guilds/{guild_id}/moderation")
async def update_moderation(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    escalation_enabled = "escalation_enabled" in form
    log_channel_raw = str(form.get("moderation_log_channel_id") or "")

    state = await load_guild_discord_state(request, guild_id)
    text_channel_ids = {c.id for c in state.text_channels}

    log_channel_id, log_error = resolve_optional_id(log_channel_raw, text_channel_ids)
    if log_error:
        context = await _page_context(guild_id, session, request, offset=0)
        context["text_channels"] = state.text_channels
        context["error"] = log_error
        return request.app.state.templates.TemplateResponse(
            request, "moderation.html", context, status_code=400
        )

    await ConfigurationService().set_moderation_log_channel(guild_id, log_channel_id)
    await ModerationService().set_escalation_enabled(guild_id, escalation_enabled)

    return RedirectResponse(f"/guilds/{guild_id}/moderation", status_code=303)


@router.post("/guilds/{guild_id}/moderation/thresholds")
async def add_escalation_threshold(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    warnings_raw = str(form.get("warnings") or "")
    action_raw = str(form.get("action") or "")

    service = ModerationService()

    async def _rerender(error: str) -> Response:
        state, context = await asyncio.gather(
            load_guild_discord_state(request, guild_id), _page_context(guild_id, session, request, offset=0)
        )
        context["text_channels"] = state.text_channels
        context["error"] = error
        return request.app.state.templates.TemplateResponse(
            request, "moderation.html", context, status_code=400
        )

    try:
        warnings = int(warnings_raw)
    except ValueError:
        return await _rerender("Warning count must be a whole number.")

    try:
        action = InfractionType(action_raw)
    except ValueError:
        return await _rerender("Escalation action must be timeout, kick, or ban.")

    try:
        await service.set_escalation_threshold(guild_id, warnings, action)
    except ModerationValidationError as exc:
        return await _rerender(str(exc))

    return RedirectResponse(f"/guilds/{guild_id}/moderation", status_code=303)


@router.post("/guilds/{guild_id}/moderation/thresholds/{warnings}/delete")
async def delete_escalation_threshold(
    guild_id: int,
    warnings: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    await ModerationService().clear_escalation_threshold(guild_id, warnings)
    return RedirectResponse(f"/guilds/{guild_id}/moderation", status_code=303)


# --- Infraction detail: view, edit reason, hard-delete ---


async def _resolve_infraction_or_404(guild_id: int, infraction_id: int):
    infraction = await ModerationService().get_infraction(guild_id, infraction_id)
    if infraction is None:
        raise HTTPException(status_code=404, detail="Infraction not found.")
    return infraction


async def _render_infraction_detail(
    request: Request, guild_id: int, session: LoadedSession, infraction, *, error: str | None = None
) -> Response:
    member_names = await _resolve_member_names(request, guild_id, [infraction])
    context = await guild_page_context(guild_id, session, "moderation")
    context.update({"infraction": infraction, "member_names": member_names, "error": error})
    status_code = 400 if error else 200
    return request.app.state.templates.TemplateResponse(
        request, "moderation_infraction_detail.html", context, status_code=status_code
    )


@router.get("/guilds/{guild_id}/moderation/infractions/{infraction_id}")
async def show_infraction_detail(
    request: Request,
    guild_id: int,
    infraction_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    infraction = await _resolve_infraction_or_404(guild_id, infraction_id)
    return await _render_infraction_detail(request, guild_id, session, infraction)


@router.post("/guilds/{guild_id}/moderation/infractions/{infraction_id}/reason")
async def update_infraction_reason(
    request: Request,
    guild_id: int,
    infraction_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    infraction = await _resolve_infraction_or_404(guild_id, infraction_id)
    form = await request.form()
    reason = str(form.get("reason") or "")

    try:
        await ModerationService().update_infraction_reason(guild_id, infraction_id, reason)
    except ModerationValidationError as exc:
        return await _render_infraction_detail(request, guild_id, session, infraction, error=str(exc))

    return RedirectResponse(f"/guilds/{guild_id}/moderation/infractions/{infraction_id}", status_code=303)


@router.post("/guilds/{guild_id}/moderation/infractions/{infraction_id}/delete")
async def delete_infraction(
    guild_id: int,
    infraction_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    deleted = await ModerationService().delete_infraction(guild_id, infraction_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Infraction not found.")
    return RedirectResponse(f"/guilds/{guild_id}/moderation", status_code=303)
