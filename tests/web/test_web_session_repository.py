from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.web_session_repository import WebSessionRepository


async def test_delete_expired_removes_only_past_sessions(db_session: AsyncSession) -> None:
    repo = WebSessionRepository(db_session)
    now = datetime.now(UTC)

    await repo.create(
        token_hash="expired",
        discord_user_id=1,
        discord_username=None,
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=now + timedelta(days=7),
        csrf_token="csrf",
        expires_at=now - timedelta(days=1),
    )
    await repo.create(
        token_hash="still-valid",
        discord_user_id=2,
        discord_username=None,
        discord_avatar_hash=None,
        access_token="at",
        refresh_token="rt",
        token_expires_at=now + timedelta(days=7),
        csrf_token="csrf",
        expires_at=now + timedelta(days=1),
    )

    # A tz-aware `now` must still correctly delete only the expired row -
    # this is the exact comparison that silently mismatched before
    # delete_expired() normalized it against SQLite's naive storage.
    deleted = await repo.delete_expired(now=now)

    assert deleted == 1
    assert await repo.get_by_token_hash("expired") is None
    assert await repo.get_by_token_hash("still-valid") is not None
