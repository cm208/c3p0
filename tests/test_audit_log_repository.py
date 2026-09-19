from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_log_entry import AuditAction, AuditTargetType
from app.db.repositories.audit_log_repository import AuditLogRepository

GUILD_A = 111
GUILD_B = 222


async def test_create_and_list(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)

    await repo.create(
        GUILD_A,
        actor_discord_user_id=1001,
        action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE,
        target_id=42,
        target_name="Staff",
        summary="Created role 'Staff'.",
    )

    entries = await repo.list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].action == AuditAction.ROLE_CREATE
    assert entries[0].target_name == "Staff"


async def test_list_for_guild_orders_newest_first(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    first = await repo.create(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="A", summary="a",
    )
    second = await repo.create(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_DELETE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="A", summary="b",
    )

    entries = await repo.list_for_guild(GUILD_A)

    assert [e.id for e in entries] == [second.id, first.id]


async def test_list_for_guild_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    await repo.create(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.CHANNEL_CREATE,
        target_type=AuditTargetType.CHANNEL, target_id=1, target_name="general", summary="a",
    )
    await repo.create(
        GUILD_B, actor_discord_user_id=1, action=AuditAction.CHANNEL_CREATE,
        target_type=AuditTargetType.CHANNEL, target_id=2, target_name="general", summary="b",
    )

    entries = await repo.list_for_guild(GUILD_A)

    assert len(entries) == 1
    assert entries[0].guild_id == GUILD_A


async def test_list_for_guild_respects_limit_and_offset(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    created = [
        await repo.create(
            GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
            target_type=AuditTargetType.ROLE, target_id=i, target_name=f"r{i}", summary="s",
        )
        for i in range(5)
    ]

    page = await repo.list_for_guild(GUILD_A, limit=2, offset=1)

    assert [e.id for e in page] == [created[3].id, created[2].id]


async def test_count_for_guild_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    await repo.create(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="a", summary="s",
    )
    await repo.create(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_DELETE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="a", summary="s",
    )
    await repo.create(
        GUILD_B, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="a", summary="s",
    )

    assert await repo.count_for_guild(GUILD_A) == 2
    assert await repo.count_for_guild(GUILD_B) == 1


async def test_count_for_guild_zero_when_none(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)

    assert await repo.count_for_guild(GUILD_A) == 0
