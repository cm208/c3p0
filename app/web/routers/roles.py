"""Self-assignable roles: full create-from-dashboard for reaction/button/select setups.

The web process has no discord.py Client, so posting a message and attaching
reactions/components is done by hand via app/web/discord_client.py's raw REST
functions and app/web/role_components.py's payload builders - both built and
verified against app/cogs/roles.py's own discord.py-side behavior byte for
byte (see those modules' docstrings). Every create/edit/delete flow below
mirrors that cog's command call order exactly, including its rough edges:

- No rollback of an already-posted message on a later failure - matches the
  cog's own *_create/*_add commands, none of which clean up on failure either.
- reactionrole_delete's DB-only cleanup (no live reaction removal) vs.
  rolebutton_delete/roleselect_delete's message.edit(view=None) are genuinely
  different behaviors in the cog, not an oversight, so they stay different here.
- A role dropdown here always means assignable_roles (hierarchy-filtered) -
  every binding type ends up calling member.add_roles()/remove_roles() in the
  cog's interaction listeners, unlike Music's DJ role or Custom Commands'
  restriction role, which are plain membership checks.
- A newly-added option to an existing (possibly disabled) group comes back
  enabled=True regardless of the group's current state - the cog's own
  rolebutton_add/roleselect_add have this exact same quirk (create_*_binding
  takes no enabled param), so it's replicated here rather than "fixed".
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.db.models.role_binding import BUTTON_CUSTOM_ID_PREFIX, RoleBindingType, select_custom_id
from app.services.role_binding_service import (
    RoleBindingService,
    RoleBindingValidationError,
    RoleBindingView,
)
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.discord_client import (
    DiscordAPIError,
    add_own_reaction,
    clear_reaction,
    edit_message_components,
    send_channel_message,
)
from app.web.guild_options import guild_page_context, load_guild_discord_state, resolve_optional_id
from app.web.message_editor import build_editor_context
from app.web.role_components import (
    ButtonSpec,
    InvalidEmojiError,
    SelectOptionSpec,
    build_button_components,
    build_select_components,
    normalize_emoji,
    reaction_path_emoji,
)
from app.web.sessions import LoadedSession

router = APIRouter()

_VALID_TYPES = {"reaction", "button", "select"}
_DEFAULT_MESSAGES = {
    "reaction": "React below to get a role!",
    "button": "Click a button below to get a role!",
    "select": "Pick your roles below!",
}
_DEFAULT_PLACEHOLDER = "Select roles..."


def _parse_type(raw: str) -> RoleBindingType:
    if raw not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail="Unknown role-binding type.")
    return RoleBindingType(raw)


async def _rebuild_components(
    http: httpx.AsyncClient,
    bot_token: str,
    *,
    interaction_type: RoleBindingType,
    channel_id: int,
    message_id: int,
    bindings: list[RoleBindingView],
    role_name_by_id: dict[int, str],
    placeholder: str = _DEFAULT_PLACEHOLDER,
) -> None:
    """Rebuild a button/select message's components from its current bindings
    (or clear them with []) - mirrors rolebutton_add/remove and
    roleselect_add/remove always rebuilding the *entire* view from
    list_for_message rather than patching in place.

    placeholder isn't a column anywhere (see role_binding.py) - every one of
    the cog's own commands that touches an existing select resets it to
    their own default unless respecified, so callers here that don't have a
    real placeholder to pass (adding/removing one option) get the same
    reset-to-default behavior rather than inventing persistence for it.
    """
    if interaction_type == RoleBindingType.BUTTON:
        specs = [
            ButtonSpec(
                label=role_name_by_id.get(b.role_id, f"role {b.role_id}"),
                custom_id=b.component_custom_id,
                emoji=b.emoji,
            )
            for b in bindings
        ]
        components = build_button_components(specs) if specs else []
    else:
        specs = [
            SelectOptionSpec(
                label=role_name_by_id.get(b.role_id, f"role {b.role_id}"), value=b.component_custom_id
            )
            for b in bindings
        ]
        components = (
            build_select_components(select_custom_id(message_id), specs, placeholder) if specs else []
        )
    await edit_message_components(http, bot_token, channel_id, message_id, components)


@router.get("/guilds/{guild_id}/roles")
async def list_roles(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    bindings, state = await asyncio.gather(
        RoleBindingService().list_bindings(guild_id), load_guild_discord_state(request, guild_id)
    )
    role_name_by_id = {r.id: r.name for r in state.all_roles}
    channel_name_by_id = {c.id: c.name for c in state.text_channels}

    grouped: dict[tuple[RoleBindingType, int], list] = {}
    for binding in bindings:
        grouped.setdefault((binding.interaction_type, binding.source_message_id), []).append(binding)

    groups = []
    for (interaction_type, message_id), group_bindings in grouped.items():
        channel_id = group_bindings[0].source_channel_id
        groups.append(
            {
                "type": interaction_type,
                "message_id": message_id,
                "channel_id": channel_id,
                "channel_name": channel_name_by_id.get(channel_id),
                "enabled": group_bindings[0].enabled,
                "options": [
                    {
                        "binding": b,
                        "role_name": role_name_by_id.get(b.role_id, f"role {b.role_id} (deleted)"),
                    }
                    for b in group_bindings
                ],
            }
        )
    groups.sort(key=lambda g: (g["type"].value, g["message_id"]))

    context = await guild_page_context(guild_id, session, "roles")
    context.update(
        {
            "groups": groups,
            "total_groups": len(groups),
            "total_options": len(bindings),
            "assignable_roles": state.assignable_roles,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "roles_list.html", context)


@router.get("/guilds/{guild_id}/roles/new")
async def new_role_form(
    request: Request,
    guild_id: int,
    type: str = "reaction",
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    binding_type = _parse_type(type)
    state = await load_guild_discord_state(request, guild_id)

    context = await guild_page_context(guild_id, session, "roles")
    context.update(
        {
            "binding_type": binding_type.value,
            "default_message": _DEFAULT_MESSAGES[binding_type.value],
            "default_placeholder": _DEFAULT_PLACEHOLDER,
            "assignable_roles": state.assignable_roles,
            "text_channels": state.text_channels,
            "editor_context": build_editor_context(
                state=state, session=session, guild_name=context["guild_name"]
            ),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "roles_new.html", context)


@router.post("/guilds/{guild_id}/roles")
async def create_role(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    binding_type = _parse_type(str(form.get("type", "")))
    channel_raw = str(form.get("channel_id") or "")
    role_raw = str(form.get("role_id") or "")
    content = str(form.get("message") or "").strip() or _DEFAULT_MESSAGES[binding_type.value]
    emoji_raw = str(form.get("emoji") or "").strip()
    placeholder = str(form.get("placeholder") or "").strip() or _DEFAULT_PLACEHOLDER
    toggle = "toggle" in form

    state = await load_guild_discord_state(request, guild_id)
    text_channel_ids = {c.id for c in state.text_channels}
    assignable_role_ids = {r.id for r in state.assignable_roles}
    role_name_by_id = {r.id: r.name for r in state.assignable_roles}

    async def _rerender(error: str) -> Response:
        context = await guild_page_context(guild_id, session, "roles")
        context.update(
            {
                "binding_type": binding_type.value,
                "default_message": _DEFAULT_MESSAGES[binding_type.value],
                "default_placeholder": _DEFAULT_PLACEHOLDER,
                "assignable_roles": state.assignable_roles,
                "text_channels": state.text_channels,
                "editor_context": build_editor_context(
                    state=state, session=session, guild_name=context["guild_name"]
                ),
                "error": error,
            }
        )
        return request.app.state.templates.TemplateResponse(
            request, "roles_new.html", context, status_code=400
        )

    channel_id, channel_error = resolve_optional_id(channel_raw, text_channel_ids)
    if channel_error or channel_id is None:
        return await _rerender(channel_error or "Select a channel.")

    role_id, role_error = resolve_optional_id(role_raw, assignable_role_ids)
    if role_error or role_id is None:
        return await _rerender(role_error or "Select a role.")

    emoji: str | None = None
    if emoji_raw or binding_type == RoleBindingType.REACTION:
        if not emoji_raw:
            return await _rerender("An emoji is required for reaction roles.")
        try:
            emoji = normalize_emoji(emoji_raw)
        except InvalidEmojiError as exc:
            return await _rerender(str(exc))

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token
    service = RoleBindingService()

    if binding_type == RoleBindingType.REACTION:
        try:
            message_id = await send_channel_message(http, bot_token, channel_id, content=content)
            await add_own_reaction(http, bot_token, channel_id, message_id, reaction_path_emoji(emoji))
        except DiscordAPIError as exc:
            return await _rerender(f"Couldn't post that message or add that reaction ({exc}).")
        try:
            await service.create_reaction_binding(
                guild_id, channel_id=channel_id, message_id=message_id, emoji=emoji,
                role_id=role_id, toggle=toggle,
            )
        except RoleBindingValidationError as exc:
            return await _rerender(str(exc))

    elif binding_type == RoleBindingType.BUTTON:
        custom_id = f"{BUTTON_CUSTOM_ID_PREFIX}{uuid4().hex}"
        components = build_button_components(
            [ButtonSpec(label=role_name_by_id[role_id], custom_id=custom_id, emoji=emoji)]
        )
        try:
            message_id = await send_channel_message(
                http, bot_token, channel_id, content=content, components=components
            )
        except DiscordAPIError as exc:
            return await _rerender(f"Couldn't post that message ({exc}).")
        try:
            await service.create_button_binding(
                guild_id, channel_id=channel_id, message_id=message_id, role_id=role_id,
                custom_id=custom_id, emoji=emoji,
            )
        except RoleBindingValidationError as exc:
            return await _rerender(str(exc))

    else:  # select
        try:
            message_id = await send_channel_message(http, bot_token, channel_id, content=content)
        except DiscordAPIError as exc:
            return await _rerender(f"Couldn't post that message ({exc}).")
        try:
            await service.create_select_binding(
                guild_id, channel_id=channel_id, message_id=message_id, role_id=role_id
            )
        except RoleBindingValidationError as exc:
            return await _rerender(str(exc))
        bindings = await service.list_for_message(guild_id, message_id, RoleBindingType.SELECT)
        try:
            await _rebuild_components(
                http, bot_token, interaction_type=RoleBindingType.SELECT, channel_id=channel_id,
                message_id=message_id, bindings=bindings, role_name_by_id=role_name_by_id,
                placeholder=placeholder,
            )
        except DiscordAPIError as exc:
            return await _rerender(f"Posted, but couldn't attach the menu ({exc}).")

    return RedirectResponse(f"/guilds/{guild_id}/roles", status_code=303)


@router.post("/guilds/{guild_id}/roles/{message_id}/options")
async def add_role_option(
    request: Request,
    guild_id: int,
    message_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    service = RoleBindingService()
    existing = await service.list_for_message(guild_id, message_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Role-binding group not found.")
    interaction_type = existing[0].interaction_type
    channel_id = existing[0].source_channel_id

    form = await request.form()
    role_raw = str(form.get("role_id") or "")
    emoji_raw = str(form.get("emoji") or "").strip()

    state = await load_guild_discord_state(request, guild_id)
    assignable_role_ids = {r.id for r in state.assignable_roles}
    role_name_by_id = {r.id: r.name for r in state.assignable_roles}

    role_id, role_error = resolve_optional_id(role_raw, assignable_role_ids)
    if role_error or role_id is None:
        raise HTTPException(status_code=400, detail=role_error or "Select a role.")

    emoji: str | None = None
    if emoji_raw or interaction_type == RoleBindingType.REACTION:
        if not emoji_raw:
            raise HTTPException(status_code=400, detail="An emoji is required for reaction roles.")
        try:
            emoji = normalize_emoji(emoji_raw)
        except InvalidEmojiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token

    try:
        if interaction_type == RoleBindingType.REACTION:
            await add_own_reaction(http, bot_token, channel_id, message_id, reaction_path_emoji(emoji))
            await service.create_reaction_binding(
                guild_id, channel_id=channel_id, message_id=message_id, emoji=emoji, role_id=role_id
            )
        else:
            if interaction_type == RoleBindingType.BUTTON:
                custom_id = f"{BUTTON_CUSTOM_ID_PREFIX}{uuid4().hex}"
                await service.create_button_binding(
                    guild_id, channel_id=channel_id, message_id=message_id, role_id=role_id,
                    custom_id=custom_id, emoji=emoji,
                )
            else:
                await service.create_select_binding(
                    guild_id, channel_id=channel_id, message_id=message_id, role_id=role_id
                )
            bindings = await service.list_for_message(guild_id, message_id, interaction_type)
            await _rebuild_components(
                http, bot_token, interaction_type=interaction_type, channel_id=channel_id,
                message_id=message_id, bindings=bindings, role_name_by_id=role_name_by_id,
            )
    except RoleBindingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DiscordAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Discord rejected the update ({exc}).") from exc

    return RedirectResponse(f"/guilds/{guild_id}/roles", status_code=303)


@router.post("/guilds/{guild_id}/roles/{message_id}/options/{binding_id}/delete")
async def delete_role_option(
    request: Request,
    guild_id: int,
    message_id: int,
    binding_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    service = RoleBindingService()
    binding = await service.get_binding(guild_id, binding_id)
    if binding is None or binding.source_message_id != message_id:
        raise HTTPException(status_code=404, detail="Role binding not found.")

    http = request.app.state.http_client
    bot_token = request.app.state.web_config.discord_bot_token

    await service.delete_binding(guild_id, binding_id)

    if binding.interaction_type == RoleBindingType.REACTION:
        # Best-effort cosmetic cleanup, matching reactionrole_remove exactly -
        # the binding is already gone regardless of whether this succeeds.
        try:
            await clear_reaction(
                http, bot_token, binding.source_channel_id, message_id, reaction_path_emoji(binding.emoji)
            )
        except DiscordAPIError:
            pass
    else:
        state = await load_guild_discord_state(request, guild_id)
        role_name_by_id = {r.id: r.name for r in state.all_roles}
        remaining = await service.list_for_message(guild_id, message_id, binding.interaction_type)
        try:
            await _rebuild_components(
                http, bot_token, interaction_type=binding.interaction_type,
                channel_id=binding.source_channel_id, message_id=message_id,
                bindings=remaining, role_name_by_id=role_name_by_id,
            )
        except DiscordAPIError as exc:
            raise HTTPException(status_code=502, detail=f"Discord rejected the update ({exc}).") from exc

    return RedirectResponse(f"/guilds/{guild_id}/roles", status_code=303)


@router.post("/guilds/{guild_id}/roles/{message_id}/toggle")
async def toggle_role_group(
    guild_id: int,
    message_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    service = RoleBindingService()
    existing = await service.list_for_message(guild_id, message_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Role-binding group not found.")

    # DB-only, matching reactionrole_enable/disable's _set_message_enabled -
    # it never touches Discord, so a disabled button/select still renders,
    # it just stops granting/removing roles when interacted with.
    await service.set_message_enabled(guild_id, message_id, not existing[0].enabled)
    return RedirectResponse(f"/guilds/{guild_id}/roles", status_code=303)


@router.post("/guilds/{guild_id}/roles/{message_id}/delete")
async def delete_role_group(
    request: Request,
    guild_id: int,
    message_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    service = RoleBindingService()
    existing = await service.list_for_message(guild_id, message_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Role-binding group not found.")
    interaction_type = existing[0].interaction_type
    channel_id = existing[0].source_channel_id

    await service.delete_message_bindings_by_type(guild_id, message_id, interaction_type)

    if interaction_type != RoleBindingType.REACTION:
        # button/select: reactionrole_delete deliberately does NOT clear live
        # reactions (matched above by doing nothing Discord-side for it),
        # but rolebutton_delete/roleselect_delete both clear components.
        http = request.app.state.http_client
        bot_token = request.app.state.web_config.discord_bot_token
        try:
            await edit_message_components(http, bot_token, channel_id, message_id, [])
        except DiscordAPIError as exc:
            raise HTTPException(status_code=502, detail=f"Discord rejected the update ({exc}).") from exc

    return RedirectResponse(f"/guilds/{guild_id}/roles", status_code=303)
