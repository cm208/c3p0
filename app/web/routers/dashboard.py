from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.services.bot_guild_service import BotGuildService
from app.web.discord_client import has_manage_access
from app.web.sessions import SESSION_COOKIE_NAME, SessionService

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    # Unauthenticated on purpose - this is only for the container
    # healthcheck, not a user-facing page.
    return {"status": "ok"}


@router.get("/")
async def index(request: Request) -> Response:
    templates = request.app.state.templates
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)

    session = None
    if raw_token:
        session_service = SessionService(request.app.state.web_config)
        session = await session_service.load(raw_token, http=request.app.state.http_client)

    if session is None:
        # A soft "please log in" page rather than require_login's hard
        # redirect - avoids a redirect loop for a first-time or logged-out
        # visitor landing on "/".
        return templates.TemplateResponse(request, "login.html", {})

    present = await BotGuildService().present_guilds()
    guilds = [
        {"id": guild_id, "name": present[guild_id]}
        for guild_id, permissions in session.guild_permissions.items()
        if guild_id in present and has_manage_access(permissions)
    ]
    guilds.sort(key=lambda g: (g["name"] or "").lower())

    return templates.TemplateResponse(
        request, "guild_list.html", {"session": session, "guilds": guilds}
    )
