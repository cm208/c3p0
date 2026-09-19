"""Shared repository base.

Repositories hide SQLAlchemy details from services. They take an
AsyncSession explicitly rather than opening their own, so callers (usually
a service, via `session_scope()`) control transaction boundaries.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
