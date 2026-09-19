from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import database
from app.db.models import Base


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests never pick up a real .env / real environment secrets."""
    for var in (
        "DISCORD_TOKEN",
        "DISCORD_APPLICATION_ID",
        "DISCORD_DEV_GUILD_IDS",
        "DATABASE_URL",
        "METRICS_HOST",
        "METRICS_PORT",
        "LOG_LEVEL",
        "LOG_HUMAN",
        "DEFAULT_PREFIX",
        "DISCORD_CLIENT_SECRET",
        "PUBLIC_BASE_URL",
        "WEB_HOST",
        "WEB_PORT",
        "WEB_COOKIE_SECURE",
        "INTERNAL_API_TOKEN",
        "INTERNAL_API_HOST",
        "INTERNAL_API_PORT",
        "BOT_INTERNAL_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """An isolated in-memory SQLite database for a single test."""
    engine = database.init_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with database.session_scope() as session:
        yield session

    await database.dispose_engine()
