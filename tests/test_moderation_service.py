from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import database
from app.db.database import session_scope
from app.db.models import Base
from app.db.models.infraction import InfractionType
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.services.moderation_service import (
    ModerationService,
    ModerationValidationError,
    parse_duration,
    validate_clear_amount,
    validate_reason,
)

GUILD_A = 111
USER_A = 1001
MODERATOR_A = 2002


# --- Pure validation/parsing (no DB) ---


def test_parse_duration_single_unit() -> None:
    assert parse_duration("10m") == 600
    assert parse_duration("1h") == 3600
    assert parse_duration("2d") == 2 * 86400


def test_parse_duration_combined_units() -> None:
    assert parse_duration("1h30m") == 3600 + 1800


def test_parse_duration_rejects_garbage() -> None:
    with pytest.raises(ModerationValidationError):
        parse_duration("not a duration")


def test_parse_duration_rejects_partial_garbage() -> None:
    with pytest.raises(ModerationValidationError):
        parse_duration("10m and then some")


def test_parse_duration_rejects_zero() -> None:
    with pytest.raises(ModerationValidationError, match="greater than zero"):
        parse_duration("0m")


def test_parse_duration_rejects_over_28_days() -> None:
    with pytest.raises(ModerationValidationError, match="28 days"):
        parse_duration("29d")


def test_validate_reason_trims_and_allows_none() -> None:
    assert validate_reason(None) is None
    assert validate_reason("   ") is None
    assert validate_reason("  spam  ") == "spam"


def test_validate_reason_rejects_too_long() -> None:
    with pytest.raises(ModerationValidationError, match="512"):
        validate_reason("x" * 513)


def test_validate_clear_amount_bounds() -> None:
    assert validate_clear_amount(1) == 1
    assert validate_clear_amount(100) == 100
    with pytest.raises(ModerationValidationError):
        validate_clear_amount(0)
    with pytest.raises(ModerationValidationError):
        validate_clear_amount(101)


# --- Service (DB-backed) ---


async def test_record_infraction_persists(db_session: AsyncSession) -> None:
    service = ModerationService()

    view = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN, reason="spam"
    )

    assert view.type == InfractionType.WARN
    assert view.reason == "spam"
    assert view.active is True


async def test_record_infraction_sets_expiry_for_timeout(db_session: AsyncSession) -> None:
    service = ModerationService()

    view = await service.record_infraction(
        GUILD_A,
        user_id=USER_A,
        moderator_id=MODERATOR_A,
        type=InfractionType.TIMEOUT,
        duration_seconds=600,
    )

    assert view.duration_seconds == 600
    assert view.expires_at is not None


async def test_list_infractions_active_only(db_session: AsyncSession) -> None:
    service = ModerationService()
    active = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
    )
    resolved = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
    )
    await service.resolve_infraction(GUILD_A, resolved.id)

    all_infractions = await service.list_infractions(GUILD_A, USER_A)
    active_only = await service.list_infractions(GUILD_A, USER_A, active_only=True)

    assert len(all_infractions) == 2
    assert [i.id for i in active_only] == [active.id]


async def test_count_infractions_for_guild(db_session: AsyncSession) -> None:
    service = ModerationService()
    await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
    )
    await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.KICK
    )

    assert await service.count_infractions_for_guild(GUILD_A) == 2
    assert await service.count_infractions_for_guild(999) == 0


async def test_resolve_active_bans(db_session: AsyncSession) -> None:
    service = ModerationService()
    await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.BAN
    )

    count = await service.resolve_active_bans(GUILD_A, USER_A)

    assert count == 1
    active = await service.list_infractions(GUILD_A, USER_A, active_only=True)
    assert active == []


async def test_get_infraction_returns_none_for_other_guild(db_session: AsyncSession) -> None:
    service = ModerationService()
    created = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
    )

    assert await service.get_infraction(999, created.id) is None
    fetched = await service.get_infraction(GUILD_A, created.id)
    assert fetched is not None
    assert fetched.id == created.id


async def test_update_infraction_reason_validates_and_persists(db_session: AsyncSession) -> None:
    service = ModerationService()
    created = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN, reason="original"
    )

    updated = await service.update_infraction_reason(GUILD_A, created.id, "corrected")
    assert updated is not None
    assert updated.reason == "corrected"

    with pytest.raises(ModerationValidationError):
        await service.update_infraction_reason(GUILD_A, created.id, "x" * 600)


async def test_delete_infraction_removes_it(db_session: AsyncSession) -> None:
    service = ModerationService()
    created = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
    )

    assert await service.delete_infraction(999, created.id) is False
    assert await service.delete_infraction(GUILD_A, created.id) is True
    assert await service.get_infraction(GUILD_A, created.id) is None


async def test_search_infractions_for_guild_applies_filters(db_session: AsyncSession) -> None:
    service = ModerationService()
    warn = await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN, reason="Spamming"
    )
    await service.record_infraction(
        GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.KICK, reason="Unrelated"
    )

    results = await service.search_infractions_for_guild(GUILD_A, text="spam")
    assert [i.id for i in results] == [warn.id]
    assert await service.count_search_infractions_for_guild(GUILD_A, text="spam") == 1


async def test_record_infraction_creates_parent_guild_config_row(tmp_path: Path) -> None:
    # Infraction.guild_id FKs to guild_config.guild_id, enforced against the
    # real sqlite file this runs against in production - unlike db_session's
    # in-memory database (used by every other test in this file), which
    # ignores FKs entirely (see test_database.py). A guild's first-ever
    # infraction can land before anyone has ever touched /config for that
    # guild, so guild_config may not exist yet; without _ensure_guild_row
    # this raises an IntegrityError against a real database.
    db_path = tmp_path / "record-infraction.db"
    database.init_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        async with database.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        service = ModerationService()
        view = await service.record_infraction(
            GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
        )

        assert view.type == InfractionType.WARN

        async with session_scope() as session:
            guild_config = await GuildConfigRepository(session).get(GUILD_A)
        assert guild_config is not None
    finally:
        await database.dispose_engine()


async def test_get_config_also_creates_parent_guild_config(db_session: AsyncSession) -> None:
    # ModerationConfig.guild_id FKs to guild_config.guild_id, enforced
    # against the real sqlite file this runs against in production (not the
    # in-memory test database, which ignores FKs) - a guild the bot just
    # joined has no guild_config row yet, so this must create one rather
    # than assume it already exists.
    service = ModerationService(default_prefix="?")

    await service.get_config(GUILD_A)

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_escalation_threshold_set_and_clear(db_session: AsyncSession) -> None:
    service = ModerationService()

    await service.set_escalation_threshold(GUILD_A, 3, InfractionType.TIMEOUT)
    config = await service.get_config(GUILD_A)
    assert config.escalation_thresholds == {"3": "timeout"}

    await service.clear_escalation_threshold(GUILD_A, 3)
    config = await service.get_config(GUILD_A)
    assert config.escalation_thresholds == {}


async def test_escalation_threshold_rejects_invalid_warnings(db_session: AsyncSession) -> None:
    service = ModerationService()

    with pytest.raises(ModerationValidationError):
        await service.set_escalation_threshold(GUILD_A, 0, InfractionType.KICK)


async def test_escalation_threshold_rejects_non_action_type(db_session: AsyncSession) -> None:
    service = ModerationService()

    with pytest.raises(ModerationValidationError, match="timeout, kick, or ban"):
        await service.set_escalation_threshold(GUILD_A, 3, InfractionType.WARN)


async def test_check_escalation_disabled_returns_no_action(db_session: AsyncSession) -> None:
    service = ModerationService()
    await service.set_escalation_threshold(GUILD_A, 1, InfractionType.TIMEOUT)
    await service.record_infraction(GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN)

    decision = await service.check_escalation(GUILD_A, USER_A)

    assert decision.action is None
    assert decision.warning_count == 1


async def test_check_escalation_fires_on_exact_threshold(db_session: AsyncSession) -> None:
    service = ModerationService()
    await service.set_escalation_enabled(GUILD_A, True)
    await service.set_escalation_threshold(GUILD_A, 2, InfractionType.KICK)
    await service.record_infraction(GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN)
    await service.record_infraction(GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN)

    decision = await service.check_escalation(GUILD_A, USER_A)

    assert decision.action == InfractionType.KICK
    assert decision.warning_count == 2


async def test_check_escalation_does_not_refire_past_threshold(db_session: AsyncSession) -> None:
    service = ModerationService()
    await service.set_escalation_enabled(GUILD_A, True)
    await service.set_escalation_threshold(GUILD_A, 2, InfractionType.KICK)
    for _ in range(3):
        await service.record_infraction(
            GUILD_A, user_id=USER_A, moderator_id=MODERATOR_A, type=InfractionType.WARN
        )

    decision = await service.check_escalation(GUILD_A, USER_A)

    assert decision.action is None
    assert decision.warning_count == 3
