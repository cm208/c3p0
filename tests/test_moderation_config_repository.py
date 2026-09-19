from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.moderation_config_repository import ModerationConfigRepository

GUILD_A = 111


async def test_get_or_create_defaults(db_session: AsyncSession) -> None:
    repo = ModerationConfigRepository(db_session)

    config = await repo.get_or_create(GUILD_A)

    assert config.guild_id == GUILD_A
    assert config.escalation_enabled is False
    assert config.escalation_thresholds == {}


async def test_get_or_create_is_idempotent(db_session: AsyncSession) -> None:
    repo = ModerationConfigRepository(db_session)

    first = await repo.get_or_create(GUILD_A)
    await repo.set_escalation_enabled(GUILD_A, True)
    second = await repo.get_or_create(GUILD_A)

    assert first.guild_id == second.guild_id
    assert second.escalation_enabled is True


async def test_set_escalation_thresholds_persists(db_session: AsyncSession) -> None:
    repo = ModerationConfigRepository(db_session)

    updated = await repo.set_escalation_thresholds(GUILD_A, {"3": "timeout", "5": "kick"})

    assert updated.escalation_thresholds == {"3": "timeout", "5": "kick"}
    reloaded = await repo.get(GUILD_A)
    assert reloaded is not None
    assert reloaded.escalation_thresholds == {"3": "timeout", "5": "kick"}
