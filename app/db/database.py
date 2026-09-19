"""Async database engine and session management.

Repositories and services should get sessions through `session_scope()`
rather than constructing engines/sessions themselves, so connection
lifecycle stays centralized.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the global async engine and session factory.

    Safe to call once at startup. Raises if called twice without
    `dispose_engine()` in between, to catch accidental re-initialization.
    """
    global _engine, _session_factory
    if _engine is not None:
        raise RuntimeError("Database engine already initialized")

    connect_args = {}
    if database_url.startswith("sqlite"):
        # Required for SQLite to behave correctly with the async driver
        # under concurrent access from multiple coroutines.
        connect_args["check_same_thread"] = False

    _engine = create_async_engine(database_url, echo=echo, connect_args=connect_args)

    if database_url.startswith("sqlite") and ":memory:" not in database_url:
        # WAL mode: readers don't block the writer and vice versa - needed
        # now that two separate processes (the bot and the web dashboard)
        # both open this same file. busy_timeout makes brief contention
        # retry instead of immediately raising "database is locked".
        # foreign_keys=ON is unrelated to concurrency but was never being
        # set at all, silently no-op'ing every ondelete="CASCADE" in the
        # schema - fixed here since this is the one place connections are
        # established. Skipped for :memory: test databases, which reject
        # WAL and don't need any of this anyway.
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialized - call init_engine() first")
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope.

    Commits on clean exit, rolls back on exception, always closes.
    """
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized - call init_engine() first")

    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
