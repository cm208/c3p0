"""General server settings: prefix, default role, log channel.

Moderation log channel lives on the Moderation page instead, even though
it's set via the same ConfigurationService - it's conceptually a
moderation setting, and General was getting crowded with fields that
belong to a specific feature area now that this dashboard has one.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from app.services.config_service import ConfigurationService, ConfigValidationError
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.guild_options import guild_page_context, load_guild_discord_state, resolve_optional_id
from app.web.sessions import LoadedSession

router = APIRouter()


@router.get("/guilds/{guild_id}/general")
async def show_general(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    config, state = await asyncio.gather(
        ConfigurationService().get_config(guild_id), load_guild_discord_state(request, guild_id)
    )

    context = await guild_page_context(guild_id, session, "general")
    context.update(
        {
            "config": config,
            "assignable_roles": state.assignable_roles,
            "text_channels": state.text_channels,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "general.html", context)


@router.post("/guilds/{guild_id}/general")
async def update_general(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    prefix = str(form.get("prefix", ""))
    default_role_raw = str(form.get("default_role_id") or "")
    log_channel_raw = str(form.get("log_channel_id") or "")

    config_service = ConfigurationService()
    state = await load_guild_discord_state(request, guild_id)
    assignable_role_ids = {r.id for r in state.assignable_roles}
    text_channel_ids = {c.id for c in state.text_channels}

    async def _rerender(error: str) -> Response:
        config = await config_service.get_config(guild_id)
        context = await guild_page_context(guild_id, session, "general")
        context.update(
            {
                "config": config,
                "assignable_roles": state.assignable_roles,
                "text_channels": state.text_channels,
                "error": error,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "general.html", context, status_code=400
        )

    role_id, role_error = resolve_optional_id(default_role_raw, assignable_role_ids)
    if role_error:
        return await _rerender(role_error)

    log_channel_id, log_error = resolve_optional_id(log_channel_raw, text_channel_ids)
    if log_error:
        return await _rerender(log_error)

    try:
        await config_service.set_prefix(guild_id, prefix)
    except ConfigValidationError as exc:
        return await _rerender(str(exc))

    await config_service.set_default_role(guild_id, role_id)
    await config_service.set_log_channel(guild_id, log_channel_id)

    return RedirectResponse(f"/guilds/{guild_id}/general", status_code=303)
