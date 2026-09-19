"""Welcome/member-join configuration: every WelcomeConfigView field.

Two fields have non-obvious semantics replicated exactly from app/cogs/welcome.py
rather than treated like ordinary dropdowns/toggles:

- role_id/role_enabled: WelcomeService.set_role() sets both together (there's
  no "pick a role but stay disabled" state), while disable_role() only ever
  flips role_enabled off and leaves role_id untouched. Enabling without a
  role selected is rejected here the same way the precondition below is.
- join_log_enabled: enabling it requires a log channel to already be set via
  General (see /welcome join-log enable's own precondition in the cog) -
  there's no channel picker on this page for it, so the dashboard must
  refuse the same way rather than silently accepting an unusable state.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from app.services.config_service import ConfigurationService
from app.services.welcome_service import WelcomeService, WelcomeValidationError
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.guild_options import guild_page_context, load_guild_discord_state, resolve_optional_id
from app.web.sessions import LoadedSession

router = APIRouter()


@router.get("/guilds/{guild_id}/welcome")
async def show_welcome(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    config, state = await asyncio.gather(
        WelcomeService().get_config(guild_id), load_guild_discord_state(request, guild_id)
    )

    context = await guild_page_context(guild_id, session, "welcome")
    context.update(
        {
            "config": config,
            "assignable_roles": state.assignable_roles,
            "text_channels": state.text_channels,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "welcome.html", context)


@router.post("/guilds/{guild_id}/welcome")
async def update_welcome(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    channel_raw = str(form.get("channel_id") or "")
    role_raw = str(form.get("role_id") or "")
    message_template = str(form.get("message_template") or "").strip()
    embed_title = str(form.get("embed_title") or "").strip()
    embed_description = str(form.get("embed_description") or "").strip()
    embed_footer = str(form.get("embed_footer") or "").strip()
    dm_template = str(form.get("dm_template") or "").strip()

    enabled = "enabled" in form
    message_enabled = "message_enabled" in form
    embed_enabled = "embed_enabled" in form
    dm_enabled = "dm_enabled" in form
    role_enabled = "role_enabled" in form
    join_log_enabled = "join_log_enabled" in form

    service = WelcomeService()
    state = await load_guild_discord_state(request, guild_id)
    text_channel_ids = {c.id for c in state.text_channels}
    assignable_role_ids = {r.id for r in state.assignable_roles}

    async def _rerender(error: str) -> Response:
        config = await service.get_config(guild_id)
        context = await guild_page_context(guild_id, session, "welcome")
        context.update(
            {
                "config": config,
                "assignable_roles": state.assignable_roles,
                "text_channels": state.text_channels,
                "error": error,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "welcome.html", context, status_code=400
        )

    channel_id, channel_error = resolve_optional_id(channel_raw, text_channel_ids)
    if channel_error:
        return await _rerender(channel_error)

    role_id, role_error = resolve_optional_id(role_raw, assignable_role_ids)
    if role_error:
        return await _rerender(role_error)

    if role_enabled and role_id is None:
        return await _rerender("Select a role before enabling the auto-role feature.")

    if join_log_enabled:
        guild_config = await ConfigurationService().get_config(guild_id)
        if guild_config.log_channel_id is None:
            return await _rerender(
                "Set a log channel on the General page before enabling join logging."
            )

    await service.set_enabled(guild_id, enabled)
    await service.set_channel(guild_id, channel_id)
    await service.set_message_enabled(guild_id, message_enabled)

    if message_template:
        try:
            await service.set_message(guild_id, message_template)
        except WelcomeValidationError as exc:
            return await _rerender(str(exc))

    await service.set_embed_enabled(guild_id, embed_enabled)

    try:
        await service.set_embed_title(guild_id, embed_title or None)
        await service.set_embed_description(guild_id, embed_description or None)
        await service.set_embed_footer(guild_id, embed_footer or None)
    except WelcomeValidationError as exc:
        return await _rerender(str(exc))

    await service.set_dm_enabled(guild_id, dm_enabled)

    if dm_template:
        try:
            await service.set_dm_message(guild_id, dm_template)
        except WelcomeValidationError as exc:
            return await _rerender(str(exc))

    if role_enabled:
        await service.set_role(guild_id, role_id)
    else:
        await service.disable_role(guild_id)

    await service.set_join_log_enabled(guild_id, join_log_enabled)

    return RedirectResponse(f"/guilds/{guild_id}/welcome", status_code=303)
