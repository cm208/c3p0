"""FastAPI dependencies for authentication and per-guild authorization.

Kept to two shallow levels (require_login -> require_guild_access) rather
than a deeper dependency-injection hierarchy - this project otherwise
avoids DI-framework-style indirection, so this stays FastAPI's minimum
idiom, not a pattern of its own.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.services.bot_guild_service import BotGuildService
from app.web.discord_client import has_manage_access
from app.web.sessions import SESSION_COOKIE_NAME, LoadedSession, SessionService


async def require_login(request: Request) -> LoadedSession:
    session_service = SessionService(request.app.state.web_config)
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)

    session = await session_service.load(raw_token, http=request.app.state.http_client)
    if session is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return session


async def require_guild_access(
    guild_id: int, session: LoadedSession = Depends(require_login)
) -> LoadedSession:
    # 404 for "bot isn't in this guild" rather than a distinct 403 -
    # doesn't leak whether a guild_id exists at all to someone probing it.
    if not await BotGuildService().is_present(guild_id):
        raise HTTPException(status_code=404)

    permissions = session.guild_permissions.get(guild_id, 0)
    if not has_manage_access(permissions):
        raise HTTPException(status_code=403, detail="You don't have permission to manage this server.")

    return session
