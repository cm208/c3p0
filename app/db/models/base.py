from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """created_at / updated_at columns shared by most tables."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class GuildScopedMixin:
    """Marker mixin for tables that must always be filtered by guild_id.

    This doesn't add a column by itself (guild_id's type/FK details differ
    slightly per table), it exists so repository code and reviewers can
    grep for `GuildScopedMixin` to confirm every guild-scoped table is
    accounted for, per the multi-guild isolation requirement.
    """
