from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.audit_log_entry import AuditAction, AuditTargetType
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.services.audit_log_service import AuditLogService

GUILD_A = 111
GUILD_B = 222


async def test_record_persists(db_session: AsyncSession) -> None:
    service = AuditLogService()

    view = await service.record(
        GUILD_A,
        actor_discord_user_id=1001,
        action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE,
        target_id=42,
        target_name="Staff",
        summary="Created role 'Staff'.",
    )

    assert view.action == AuditAction.ROLE_CREATE
    entries = await service.list_for_guild(GUILD_A)
    assert len(entries) == 1
    assert entries[0].id == view.id


async def test_record_also_creates_parent_guild_config(db_session: AsyncSession) -> None:
    # AuditLogEntry.guild_id FKs to guild_config.guild_id, enforced against
    # the real sqlite file this runs against in production (not the
    # in-memory test database, which ignores FKs) - a dashboard action can
    # be the very first write ever made for a guild.
    service = AuditLogService(default_prefix="?")

    await service.record(
        GUILD_A,
        actor_discord_user_id=1001,
        action=AuditAction.CHANNEL_CREATE,
        target_type=AuditTargetType.CHANNEL,
        target_id=1,
        target_name="general",
        summary="Created channel 'general'.",
    )

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_list_for_guild_scoped_to_guild(db_session: AsyncSession) -> None:
    service = AuditLogService()
    await service.record(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="a", summary="s",
    )
    await service.record(
        GUILD_B, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="a", summary="s",
    )

    entries = await service.list_for_guild(GUILD_A)

    assert len(entries) == 1
    assert entries[0].guild_id == GUILD_A


async def test_count_for_guild(db_session: AsyncSession) -> None:
    service = AuditLogService()
    await service.record(
        GUILD_A, actor_discord_user_id=1, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=1, target_name="a", summary="s",
    )

    assert await service.count_for_guild(GUILD_A) == 1
    assert await service.count_for_guild(GUILD_B) == 0
