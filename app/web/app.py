"""FastAPI app factory for the web dashboard.

Deliberately does NOT call init_engine()/dispose_engine() itself - the
caller (app/web/__main__.py in production, or a test) owns the database
engine's lifecycle, exactly like app/__main__.py does for the bot process.
That keeps create_app() safe to use in tests that also seed data through
the same globally-initialized engine via the `db_session` fixture.
"""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.web.config import WebConfig
from app.web.formatting import format_duration, progress_percent
from app.web.message_editor import ALL_VARIABLES, DM_VARIABLES
from app.web.routers import (
    auth,
    console,
    custom_commands,
    dashboard,
    general,
    moderation,
    music,
    roles,
    server_management,
    welcome,
)

_WEB_DIR = Path(__file__).resolve().parent


def create_app(
    config: WebConfig,
    *,
    http_client: httpx.AsyncClient | None = None,
    bot_http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the dashboard app.

    `http_client` (Discord's REST API, ~10s timeout) and `bot_http_client`
    (the bot's own internal music control-plane API, app/music/
    internal_api.py - given a much longer timeout since a yt-dlp-backed
    enqueue call routinely takes several seconds) can each be supplied by
    tests (e.g. wired to httpx.MockTransport) to avoid ever making a real
    call; when omitted, a real client is created and owned (opened/closed)
    by this app's lifespan.
    """
    owns_http_client = http_client is None
    owns_bot_http_client = bot_http_client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http_client = http_client or httpx.AsyncClient(timeout=10.0)
        app.state.bot_http_client = bot_http_client or httpx.AsyncClient(timeout=30.0)
        try:
            yield
        finally:
            if owns_http_client:
                await app.state.http_client.aclose()
            if owns_bot_http_client:
                await app.state.bot_http_client.aclose()

    app = FastAPI(title="C3P0 Dashboard", lifespan=lifespan)
    app.state.web_config = config
    app.state.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.state.templates.env.filters["duration"] = format_duration
    app.state.templates.env.globals["progress_percent"] = progress_percent
    app.state.templates.env.globals["app_version"] = __version__
    # data-variables values for message_editor.js textareas (see app/web/message_editor.py).
    app.state.templates.env.globals["editor_variables"] = {"all": ALL_VARIABLES, "dm": DM_VARIABLES}
    # One value per process start, appended as ?v=... on every static asset
    # URL (see base.html/music.html) - forces browsers to fetch fresh
    # style.css/music.js on the very next page load after a deploy, instead
    # of serving a stale cached copy indefinitely (StaticFiles sets
    # Last-Modified/ETag but no Cache-Control, so browsers are otherwise
    # free to skip revalidation for a while on their own heuristics).
    app.state.templates.env.globals["asset_version"] = secrets.token_hex(4)

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.middleware("http")
    async def _cache_versioned_static_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            # Safe to cache "forever" specifically because the URL always
            # carries ?v=<per-process-start token> - a new deploy means a
            # new token, which means a new URL, which a long-lived cache
            # entry for the *old* URL can never shadow.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    app.include_router(dashboard.router)
    app.include_router(auth.router)
    app.include_router(console.router)
    app.include_router(general.router)
    app.include_router(welcome.router)
    app.include_router(music.router)
    app.include_router(custom_commands.router)
    app.include_router(moderation.router)
    app.include_router(roles.router)
    app.include_router(server_management.router)

    return app
