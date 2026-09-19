"""CSRF protection for mutating (POST) requests.

Login CSRF (the OAuth `state` param) is handled directly in
routers/auth.py, since it's a one-shot check tied to the redirect flow
rather than a per-session token. This module covers the ongoing case:
protecting the dashboard's own POST forms once a session exists.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request

from app.web.dependencies import require_login
from app.web.sessions import LoadedSession


async def require_csrf(
    request: Request, session: LoadedSession = Depends(require_login)
) -> LoadedSession:
    form = await request.form()
    submitted = form.get("csrf_token")
    if not isinstance(submitted, str) or not hmac.compare_digest(submitted, session.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    return session
