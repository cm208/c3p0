from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class WebSession(Base, TimestampMixin):
    """A logged-in dashboard session.

    Server-side by design: the cookie only carries an opaque random token,
    never anything the client could read or tamper with. Only a sha256
    hash of that token is stored here, so a copy of this table alone
    (a DB dump, a backup) can't be used to hijack a live session.

    access_token/refresh_token are stored as plaintext - the same trust
    boundary as the bot token already living in plaintext `.env` on this
    host. That's a deliberate, revisitable trade-off for a self-hosted
    single-tenant deployment, not an oversight; encrypting these at rest
    is a reasonable future hardening pass if this is ever exposed beyond
    a small group of trusted admins.

    guild_permissions_cache exists so a Discord permission check doesn't
    require an API call on every single request - it's a cache with a
    short TTL (see app/web/config.py), not a source of truth. Permissions
    can be revoked in Discord at any time, so this must expire quickly
    rather than being trusted for the life of the session.
    """

    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    discord_username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    discord_avatar_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # {"<guild_id>": <permissions_int>} for every guild the user is in,
    # as of guild_permissions_cached_at.
    guild_permissions_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    guild_permissions_cached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    csrf_token: Mapped[str] = mapped_column(String(64))

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    def __repr__(self) -> str:
        return f"WebSession(discord_user_id={self.discord_user_id})"
