from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.web_session_repository import WebSessionRepository
from app.web.config import WebConfig

SeedSession = Callable[..., Awaitable[str]]


@pytest.fixture
def web_config() -> WebConfig:
    return WebConfig(
        discord_client_id=123,
        discord_client_secret="secret",
        discord_bot_token="bot-token",
        public_base_url="https://example.com",
        internal_api_token="internal-secret",
        cookie_secure=False,
    )


@pytest.fixture
def seed_session() -> SeedSession:
    """Factory fixture: `await seed_session(db_session, ...)` creates a
    WebSession row and returns its csrf_token, shared across every
    router test file rather than each one hand-rolling this."""

    async def _seed(
        db_session: AsyncSession,
        *,
        raw_token: str = "good-token",
        permissions: dict[str, int] | None = None,
        discord_user_id: int = 42,
        discord_username: str = "volvo",
    ) -> str:
        now = datetime.now(UTC)
        record = await WebSessionRepository(db_session).create(
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            discord_avatar_hash=None,
            access_token="at",
            refresh_token="rt",
            token_expires_at=now + timedelta(days=7),
            csrf_token="the-csrf-token",
            expires_at=now + timedelta(days=30),
        )
        record.guild_permissions_cache = permissions if permissions is not None else {}
        record.guild_permissions_cached_at = now
        await db_session.flush()
        return record.csrf_token

    return _seed
