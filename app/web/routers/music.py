"""Music: configuration, plus live now-playing/queue/transport controls.

Config (enabled/default_volume/max_queue_size/dj_role_id) is handled the
same way every other guild setting is - a plain form POST against
MusicService. dj_role_id is deliberately validated against all_roles, not
the hierarchy-filtered assignable_roles every other role dropdown on this
dashboard uses - _check_dj (app/cogs/music.py) is a plain `role in
ctx.author.roles` membership check, never `add_roles()`, so a role above
the bot's own top role is still a completely valid DJ role.

Live state/controls (now-playing, queue, pause/resume/skip, volume) go
through app/web/bot_client.py to the bot process's internal control-plane
API (app/music/internal_api.py) - this web process has no discord.py
Client/GuildPlayer of its own. Every mutating route below requires
require_guild_access (Manage Guild) exactly like every other dashboard
control; no separate DJ-role check is layered on top, since
MusicCog._check_dj already treats Manage Guild as an automatic pass, so
reaching this page at all already clears that bar.

Cold-start (nothing playing yet): the voice-channel picker is only
rendered when the player isn't connected, and the picker's choice is only
honored by the internal API's /enqueue endpoint on that same condition -
once connected, further "add to queue" calls ignore any channel choice,
exactly like MusicCog.play does.

music_channel_id: when set, the bot posts a short message into that
channel whenever a track is queued from *this dashboard* (never for a
Discord !play - MusicCog.play already confirms in the channel the command
was typed in, so mirroring that here would double-post). See
app/music/internal_api.py's _announce_web_enqueue - the actual Discord
message-sending happens there, inside the bot process, since this web
process has no discord.py Client of its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.services.music_service import MusicService, MusicValidationError
from app.web import bot_client
from app.web.csrf import require_csrf
from app.web.dependencies import require_guild_access
from app.web.guild_options import guild_page_context, load_guild_discord_state, resolve_optional_id
from app.web.sessions import LoadedSession

router = APIRouter()


async def _load_player_snapshot(request: Request, guild_id: int) -> bot_client.PlayerStateView:
    config = request.app.state.web_config
    http = request.app.state.bot_http_client
    try:
        return await bot_client.get_player_state(
            http, config.bot_internal_base_url, config.internal_api_token, guild_id
        )
    except bot_client.BotAPIError:
        return bot_client.UNREACHABLE_STATE


async def _load_voice_channels(request: Request, guild_id: int) -> list[bot_client.VoiceChannelView]:
    config = request.app.state.web_config
    http = request.app.state.bot_http_client
    try:
        return await bot_client.list_voice_channels(
            http, config.bot_internal_base_url, config.internal_api_token, guild_id
        )
    except bot_client.BotAPIError:
        return []


async def _render_context(
    request: Request, guild_id: int, session: LoadedSession, *, error: str | None = None
) -> dict:
    # The DB config read, the Discord roles/channels fetch, and the bot's
    # own player-state call are all independent - run concurrently. Only
    # the voice-channel list depends on the result above (only needed/
    # fetched when not yet connected), so it stays a follow-up call.
    music_config, discord_state, player_state = await asyncio.gather(
        MusicService().get_config(guild_id),
        load_guild_discord_state(request, guild_id),
        _load_player_snapshot(request, guild_id),
    )
    voice_channels = [] if player_state.connected else await _load_voice_channels(request, guild_id)

    context = await guild_page_context(guild_id, session, "music")
    context.update(
        {
            "config": music_config,
            "all_roles": discord_state.all_roles,
            "text_channels": discord_state.text_channels,
            "state": player_state,
            "voice_channels": voice_channels,
            "error": error,
        }
    )
    return context


@router.get("/guilds/{guild_id}/music")
async def show_music(
    request: Request, guild_id: int, session: LoadedSession = Depends(require_guild_access)
) -> Response:
    context = await _render_context(request, guild_id, session)
    return request.app.state.templates.TemplateResponse(request, "music.html", context)


@router.get("/guilds/{guild_id}/music/state")
async def music_state(
    request: Request, guild_id: int, session: LoadedSession = Depends(require_guild_access)
) -> Response:
    """Polled every few seconds by static/music.js to keep the now-playing/
    queue panel live without a full page reload."""
    state = await _load_player_snapshot(request, guild_id)
    return JSONResponse(asdict(state))


@router.post("/guilds/{guild_id}/music")
async def update_music(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    enabled = "enabled" in form
    volume_raw = str(form.get("default_volume") or "")
    queue_size_raw = str(form.get("max_queue_size") or "")
    dj_role_raw = str(form.get("dj_role_id") or "")
    music_channel_raw = str(form.get("music_channel_id") or "")

    service = MusicService()
    discord_state = await load_guild_discord_state(request, guild_id)
    all_role_ids = {r.id for r in discord_state.all_roles}
    text_channel_ids = {c.id for c in discord_state.text_channels}

    async def _rerender(error: str) -> Response:
        context = await _render_context(request, guild_id, session, error=error)
        return request.app.state.templates.TemplateResponse(
            request, "music.html", context, status_code=400
        )

    try:
        volume = int(volume_raw)
    except ValueError:
        return await _rerender("Volume must be a whole number.")

    try:
        queue_size = int(queue_size_raw)
    except ValueError:
        return await _rerender("Queue size must be a whole number.")

    dj_role_id, dj_role_error = resolve_optional_id(dj_role_raw, all_role_ids)
    if dj_role_error:
        return await _rerender(dj_role_error)

    music_channel_id, music_channel_error = resolve_optional_id(music_channel_raw, text_channel_ids)
    if music_channel_error:
        return await _rerender(music_channel_error)

    try:
        await service.set_default_volume(guild_id, volume)
        await service.set_max_queue_size(guild_id, queue_size)
    except MusicValidationError as exc:
        return await _rerender(str(exc))

    await service.set_enabled(guild_id, enabled)
    await service.set_dj_role(guild_id, dj_role_id)
    await service.set_music_channel(guild_id, music_channel_id)

    return RedirectResponse(f"/guilds/{guild_id}/music", status_code=303)


@router.post("/guilds/{guild_id}/music/queue/add")
async def add_to_queue(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    query = str(form.get("query") or "").strip()
    channel_raw = str(form.get("voice_channel_id") or "").strip()

    async def _rerender(error: str) -> Response:
        context = await _render_context(request, guild_id, session, error=error)
        return request.app.state.templates.TemplateResponse(
            request, "music.html", context, status_code=400
        )

    if not query:
        return await _rerender("Enter a song name or URL.")

    try:
        voice_channel_id = int(channel_raw) if channel_raw else None
    except ValueError:
        return await _rerender("Invalid channel selection.")

    config = request.app.state.web_config
    http = request.app.state.bot_http_client
    try:
        await bot_client.enqueue(
            http,
            config.bot_internal_base_url,
            config.internal_api_token,
            guild_id,
            query=query,
            requested_by=session.discord_user_id,
            voice_channel_id=voice_channel_id,
        )
    except bot_client.BotAPIError as exc:
        return await _rerender(str(exc))

    return RedirectResponse(f"/guilds/{guild_id}/music", status_code=303)


@router.post("/guilds/{guild_id}/music/queue/{index}/remove")
async def remove_from_queue(
    request: Request,
    guild_id: int,
    index: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    config = request.app.state.web_config
    http = request.app.state.bot_http_client
    try:
        await bot_client.remove_queue_item(
            http, config.bot_internal_base_url, config.internal_api_token, guild_id, index
        )
    except bot_client.BotAPINotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except bot_client.BotAPIError as exc:
        context = await _render_context(request, guild_id, session, error=str(exc))
        return request.app.state.templates.TemplateResponse(
            request, "music.html", context, status_code=400
        )

    return RedirectResponse(f"/guilds/{guild_id}/music", status_code=303)


async def _control(
    request: Request, guild_id: int, session: LoadedSession, action: str
) -> Response:
    call = {"pause": bot_client.pause, "resume": bot_client.resume, "skip": bot_client.skip}[action]
    config = request.app.state.web_config
    http = request.app.state.bot_http_client
    try:
        await call(http, config.bot_internal_base_url, config.internal_api_token, guild_id)
    except bot_client.BotAPIError as exc:
        context = await _render_context(request, guild_id, session, error=str(exc))
        return request.app.state.templates.TemplateResponse(
            request, "music.html", context, status_code=400
        )

    return RedirectResponse(f"/guilds/{guild_id}/music", status_code=303)


@router.post("/guilds/{guild_id}/music/player/pause")
async def pause_player(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    return await _control(request, guild_id, session, "pause")


@router.post("/guilds/{guild_id}/music/player/resume")
async def resume_player(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    return await _control(request, guild_id, session, "resume")


@router.post("/guilds/{guild_id}/music/player/skip")
async def skip_player(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    return await _control(request, guild_id, session, "skip")


@router.post("/guilds/{guild_id}/music/player/volume")
async def set_player_volume(
    request: Request,
    guild_id: int,
    session: LoadedSession = Depends(require_guild_access),
    _csrf_ok: LoadedSession = Depends(require_csrf),
) -> Response:
    form = await request.form()
    percent_raw = str(form.get("percent") or "")

    async def _rerender(error: str) -> Response:
        context = await _render_context(request, guild_id, session, error=error)
        return request.app.state.templates.TemplateResponse(
            request, "music.html", context, status_code=400
        )

    try:
        percent = int(percent_raw)
    except ValueError:
        return await _rerender("Volume must be a whole number.")

    if not 0 <= percent <= 100:
        return await _rerender("Volume must be between 0 and 100.")

    config = request.app.state.web_config
    http = request.app.state.bot_http_client
    try:
        await bot_client.set_volume(
            http, config.bot_internal_base_url, config.internal_api_token, guild_id, percent
        )
    except bot_client.BotAPIError as exc:
        return await _rerender(str(exc))

    return RedirectResponse(f"/guilds/{guild_id}/music", status_code=303)
