from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.db.models.web_session import WebSession
from app.db.repositories.base import BaseRepository


class WebSessionRepository(BaseRepository):
    async def create(
        self,
        *,
        token_hash: str,
        discord_user_id: int,
        discord_username: str | None,
        discord_avatar_hash: str | None,
        access_token: str,
        refresh_token: str,
        token_expires_at: datetime,
        csrf_token: str,
        expires_at: datetime,
    ) -> WebSession:
        session = WebSession(
            token_hash=token_hash,
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            discord_avatar_hash=discord_avatar_hash,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def get_by_token_hash(self, token_hash: str) -> WebSession | None:
        result = await self.session.execute(
            select(WebSession).where(WebSession.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def touch(self, session_id: int, *, now: datetime) -> None:
        record = await self.session.get(WebSession, session_id)
        if record is not None:
            record.last_seen_at = now
            await self.session.flush()

    async def update_guild_permissions_cache(
        self, session_id: int, *, cache: dict[str, int], cached_at: datetime
    ) -> None:
        record = await self.session.get(WebSession, session_id)
        if record is not None:
            record.guild_permissions_cache = cache
            record.guild_permissions_cached_at = cached_at
            await self.session.flush()

    async def update_tokens(
        self,
        session_id: int,
        *,
        access_token: str,
        refresh_token: str,
        token_expires_at: datetime,
    ) -> None:
        record = await self.session.get(WebSession, session_id)
        if record is not None:
            record.access_token = access_token
            record.refresh_token = refresh_token
            record.token_expires_at = token_expires_at
            await self.session.flush()

    async def delete(self, session_id: int) -> None:
        record = await self.session.get(WebSession, session_id)
        if record is not None:
            await self.session.delete(record)
            await self.session.flush()

    async def delete_expired(self, *, now: datetime) -> int:
        # SQLite stores DateTime(timezone=True) values as naive strings
        # (see app/web/sessions.py's _as_utc for the full explanation) - a
        # tz-aware bind parameter here would compare inconsistently against
        # them, so match the storage format regardless of what's passed in.
        naive_now = now.replace(tzinfo=None) if now.tzinfo is not None else now
        result = await self.session.execute(
            delete(WebSession).where(WebSession.expires_at < naive_now)
        )
        await self.session.flush()
        return result.rowcount or 0
