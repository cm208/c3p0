"""Discord OAuth2 login/callback/logout.

Login CSRF: /auth/login sets a short-lived, HttpOnly `oauth_state` cookie
holding a random value and sends the same value as the `state` query
param to Discord. /auth/callback rejects anything where the returned
`state` doesn't match that cookie - this is what stops an attacker from
tricking a victim into completing an OAuth flow initiated by the
attacker (login CSRF), distinct from the ongoing per-session CSRF
protection in app/web/csrf.py.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app.web import discord_client
from app.web.sessions import SESSION_COOKIE_NAME, SessionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

_STATE_COOKIE_NAME = "oauth_state"
_STATE_COOKIE_MAX_AGE = 300


@router.get("/login")
async def login(request: Request) -> Response:
    config = request.app.state.web_config
    state = secrets.token_urlsafe(24)

    authorize_url = discord_client.build_authorize_url(
        client_id=config.discord_client_id,
        redirect_uri=config.oauth_redirect_uri,
        state=state,
    )

    response = RedirectResponse(authorize_url, status_code=303)
    response.set_cookie(
        _STATE_COOKIE_NAME,
        state,
        max_age=_STATE_COOKIE_MAX_AGE,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
    config = request.app.state.web_config
    expected_state = request.cookies.get(_STATE_COOKIE_NAME)

    if (
        not code
        or not state
        or not expected_state
        or not hmac.compare_digest(state, expected_state)
    ):
        response = PlainTextResponse(
            "Login attempt expired or is invalid. Please try logging in again.",
            status_code=400,
        )
        response.delete_cookie(_STATE_COOKIE_NAME)
        return response

    http = request.app.state.http_client
    try:
        tokens = await discord_client.exchange_code(
            http,
            client_id=config.discord_client_id,
            client_secret=config.discord_client_secret,
            redirect_uri=config.oauth_redirect_uri,
            code=code,
        )
        user = await discord_client.fetch_current_user(http, tokens.access_token)
    except discord_client.DiscordAPIError:
        logger.exception("Discord OAuth exchange failed")
        response = PlainTextResponse(
            "Discord login failed. Please try again.", status_code=502
        )
        response.delete_cookie(_STATE_COOKIE_NAME)
        return response

    session_service = SessionService(config)
    raw_token = await session_service.create_session(tokens=tokens, user=user)

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(_STATE_COOKIE_NAME)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=config.session_ttl_days * 86400,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    session_service = SessionService(request.app.state.web_config)
    await session_service.delete(request.cookies.get(SESSION_COOKIE_NAME))

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
