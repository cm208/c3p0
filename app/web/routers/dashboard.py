from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app import __version__
from app.services.bot_guild_service import BotGuildService
from app.web.discord_client import build_invite_url, has_manage_access
from app.web.sessions import SESSION_COOKIE_NAME, SessionService

router = APIRouter()

# The bot's cogs, for the boot log on the login screen. The web process
# has no view of what the bot actually loaded, so this mirrors
# app.bot.INITIAL_EXTENSIONS (test_dashboard_router.py keeps them in sync).
BOOT_COGS = ("admin", "welcome", "roles", "moderation", "custom_commands", "music")
_BOOT_WIDTH = 33


def _boot_line(label: str, status: str) -> str:
    return f"{label} ".ljust(_BOOT_WIDTH, ".") + f" {status}"


def boot_lines() -> list[str]:
    return [
        f"C3P0 BIOS v{__version__}  (c) 1983-2026 C3P0 SYSTEMS",
        _boot_line("MEM CHECK", "640K OK"),
        _boot_line("LOADING discord.py", "OK"),
        *(_boot_line(f"LOADING cogs/{cog}", "OK") for cog in BOOT_COGS),
        _boot_line("MOUNTING web/dashboard", "OK"),
        _boot_line("OPERATOR SESSION", "NONE"),
    ]


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
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "boot_lines": boot_lines(),
                "session_ttl_days": request.app.state.web_config.session_ttl_days,
                "console_page": "login",
            },
        )

    present = await BotGuildService().present_guilds()
    # Names and approximate member/online counts come from Discord; if that
    # call fails the picker still works, just without counts or the
    # not-yet-installed servers (whose names only Discord knows).
    summaries = await session_service.fetch_guild_summaries(session, http=request.app.state.http_client)
    summary_by_id = {g.id: g for g in summaries or []}

    guilds = []
    for guild_id, permissions in session.guild_permissions.items():
        if not has_manage_access(permissions):
            continue
        summary = summary_by_id.get(guild_id)
        installed = guild_id in present
        if not installed and summary is None:
            continue
        guilds.append(
            {
                "id": guild_id,
                "name": (present.get(guild_id) if installed else None) or (summary.name if summary else None),
                "installed": installed,
                "members": summary.approximate_member_count if summary else None,
                "online": summary.approximate_presence_count if summary else None,
                "invite_url": None
                if installed
                else build_invite_url(client_id=request.app.state.web_config.discord_client_id, guild_id=guild_id),
            }
        )
    # Installed first (the ones you can actually connect to), then by name.
    guilds.sort(key=lambda g: (not g["installed"], (g["name"] or "").lower()))
    max_members = max((g["members"] or 0 for g in guilds), default=0)

    return templates.TemplateResponse(
        request,
        "guild_list.html",
        {"session": session, "guilds": guilds, "max_members": max_members, "console_page": "guilds"},
    )
