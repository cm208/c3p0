"""Custom commands: list+create, per-trigger edit, delete.

Permission-name validation (against discord.Permissions.VALID_FLAGS) lives
here rather than in CustomCommandService, mirroring where it already lives
in app/cogs/custom_commands.py - the service is deliberately discord.py-free
per its own docstring ("no discord.py types").

Triggers, not database ids, are the URL key throughout - matching how the
Discord-side commands already address a command. A trigger may contain any
non-space character up to 80 characters, including "/", which the default
FastAPI path converter can't match inside a single {trigger} segment; such a
trigger (unusual, but not rejected by CustomCommandService's own validation)
simply isn't reachable from this page. No embed builder beyond the toggle -
CustomCommand.embed_config has zero reads anywhere and is dead.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import discord
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.services.custom_command_service import CustomCommandService, CustomCommandValidationError
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.guild_options import guild_page_context, load_guild_discord_state, resolve_optional_id
from app.web.sessions import LoadedSession

router = APIRouter()

PERMISSION_CHOICES = sorted(discord.Permissions.VALID_FLAGS)


@router.get("/guilds/{guild_id}/custom-commands")
async def list_custom_commands(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    commands_list = await CustomCommandService().list_for_guild(guild_id)
    context = await guild_page_context(guild_id, session, "custom-commands")
    context["commands"] = commands_list
    return request.app.state.templates.TemplateResponse(request, "custom_commands_list.html", context)


@router.post("/guilds/{guild_id}/custom-commands")
async def create_custom_command(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    name = str(form.get("name", ""))
    trigger = str(form.get("trigger", ""))
    response_text = str(form.get("response", ""))

    service = CustomCommandService()
    try:
        await service.create(
            guild_id,
            name=name,
            trigger=trigger,
            response=response_text,
            created_by=session.discord_user_id,
        )
    except CustomCommandValidationError as exc:
        commands_list = await service.list_for_guild(guild_id)
        context = await guild_page_context(guild_id, session, "custom-commands")
        context.update(
            {
                "commands": commands_list,
                "error": str(exc),
                "form_name": name,
                "form_trigger": trigger,
                "form_response": response_text,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "custom_commands_list.html", context, status_code=400
        )

    return RedirectResponse(f"/guilds/{guild_id}/custom-commands", status_code=303)


@router.get("/guilds/{guild_id}/custom-commands/{trigger}")
async def show_custom_command(
    request: Request,
    guild_id: int,
    trigger: str,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    command, state = await asyncio.gather(
        CustomCommandService().get_by_trigger(guild_id, trigger), load_guild_discord_state(request, guild_id)
    )
    if command is None:
        raise HTTPException(status_code=404, detail="Custom command not found.")

    context = await guild_page_context(guild_id, session, "custom-commands")
    context.update(
        {
            "command": command,
            "all_roles": state.all_roles,
            "permission_choices": PERMISSION_CHOICES,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "custom_command_edit.html", context)


@router.post("/guilds/{guild_id}/custom-commands/{trigger}")
async def update_custom_command(
    request: Request,
    guild_id: int,
    trigger: str,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    service = CustomCommandService()
    command = await service.get_by_trigger(guild_id, trigger)
    if command is None:
        raise HTTPException(status_code=404, detail="Custom command not found.")

    form = await request.form()
    enabled = "enabled" in form
    embed_enabled = "embed_enabled" in form
    usage_logging_enabled = "usage_logging_enabled" in form
    response_text = str(form.get("response", ""))
    restriction_type = str(form.get("restriction_type", "public"))
    restricted_role_raw = str(form.get("restricted_role_id") or "")
    restricted_permission = str(form.get("restricted_permission") or "").strip() or None
    cooldown_type = str(form.get("cooldown_type", "none"))
    cooldown_seconds_raw = str(form.get("cooldown_seconds") or "0")

    state = await load_guild_discord_state(request, guild_id)
    all_role_ids = {r.id for r in state.all_roles}

    async def _rerender(error: str) -> Response:
        current = await service.get_by_trigger(guild_id, trigger)
        context = await guild_page_context(guild_id, session, "custom-commands")
        context.update(
            {
                "command": current,
                "all_roles": state.all_roles,
                "permission_choices": PERMISSION_CHOICES,
                "error": error,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "custom_command_edit.html", context, status_code=400
        )

    restricted_role_id: int | None = None
    if restriction_type == "role":
        restricted_role_id, role_error = resolve_optional_id(restricted_role_raw, all_role_ids)
        if role_error:
            return await _rerender(role_error)
        if restricted_role_id is None:
            return await _rerender("Select a role for a role-restricted command.")

    if restriction_type == "permission" and restricted_permission not in PERMISSION_CHOICES:
        return await _rerender(f"`{restricted_permission}` isn't a real Discord permission.")

    try:
        cooldown_seconds = int(cooldown_seconds_raw)
    except ValueError:
        return await _rerender("Cooldown seconds must be a whole number.")

    try:
        await service.set_response_by_trigger(guild_id, trigger, response_text)
        await service.set_restriction_by_trigger(
            guild_id,
            trigger,
            restriction_type=restriction_type,
            restricted_role_id=restricted_role_id,
            restricted_permission=restricted_permission,
        )
        await service.set_cooldown_by_trigger(
            guild_id, trigger, cooldown_type=cooldown_type, cooldown_seconds=cooldown_seconds
        )
    except CustomCommandValidationError as exc:
        return await _rerender(str(exc))

    await service.set_embed_enabled_by_trigger(guild_id, trigger, embed_enabled)
    await service.set_usage_logging_by_trigger(guild_id, trigger, usage_logging_enabled)
    await service.set_enabled_by_trigger(guild_id, trigger, enabled)

    return RedirectResponse(
        f"/guilds/{guild_id}/custom-commands/{quote(trigger, safe='')}", status_code=303
    )


@router.post("/guilds/{guild_id}/custom-commands/{trigger}/delete")
async def delete_custom_command(
    guild_id: int,
    trigger: str,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    deleted = await CustomCommandService().delete_by_trigger(guild_id, trigger)
    if not deleted:
        raise HTTPException(status_code=404, detail="Custom command not found.")
    return RedirectResponse(f"/guilds/{guild_id}/custom-commands", status_code=303)
