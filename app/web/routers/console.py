"""JSON endpoints behind the console chrome every dashboard page shares.

- GET /api/status: the status bar's live bot values (version, shard,
  gateway latency, uptime), polled by static/console.js. Any logged-in
  operator may read it; it's bot-wide, not guild data. Cached briefly in
  the web process so every open tab polling it doesn't each hit the bot.
- GET /guilds/{id}/events?after=<id>: the SYSLOG column's feed, polled by
  static/syslog.js. Rows are written by the bot process (see
  app/services/event_log_service.py); this only reads them.

Neither endpoint mutates anything. Console commands that change data POST
to the same routes the pages' forms use - there is deliberately no second
write API for the console.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.services.event_log_service import EventLogService
from app.web import bot_client
from app.web.dependencies import require_guild_access, require_login
from app.web.sessions import LoadedSession

router = APIRouter()

STATUS_CACHE_SECONDS = 5.0


async def load_bot_status(request: Request) -> bot_client.BotStatusView | None:
    """The bot's /status, or None if it can't be reached right now. Shared
    with pages that render uptime server-side (Server Management's tiles)."""
    cached = getattr(request.app.state, "bot_status_cache", None)
    now = time.monotonic()
    if cached is not None and now - cached[0] < STATUS_CACHE_SECONDS:
        return cached[1]

    config = request.app.state.web_config
    try:
        status = await bot_client.get_bot_status(
            request.app.state.bot_http_client, config.bot_internal_base_url, config.internal_api_token
        )
    except bot_client.BotAPIError:
        status = None
    request.app.state.bot_status_cache = (now, status)
    return status


@router.get("/api/status")
async def bot_status(request: Request, session: LoadedSession = Depends(require_login)) -> Response:
    status = await load_bot_status(request)
    if status is None:
        return JSONResponse({"reachable": False})
    return JSONResponse({"reachable": True, **asdict(status)})


@router.get("/guilds/{guild_id}/events")
async def guild_events(
    guild_id: int,
    after: int = 0,
    session: LoadedSession = Depends(require_guild_access),
) -> Response:
    events = await EventLogService().list_after(guild_id, after_id=max(0, after))
    return JSONResponse(
        [
            {"id": e.id, "ts": e.created_at.isoformat(), "tag": e.tag, "text": e.text}
            for e in events
        ]
    )
