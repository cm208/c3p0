from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bot_guild_service import BotGuildService

GUILD_A = 111
GUILD_B = 222


async def test_mark_present_and_is_present(db_session: AsyncSession) -> None:
    service = BotGuildService()

    await service.mark_present(GUILD_A, "Test Guild")

    assert await service.is_present(GUILD_A) is True
    assert await service.is_present(GUILD_B) is False


async def test_mark_absent(db_session: AsyncSession) -> None:
    service = BotGuildService()
    await service.mark_present(GUILD_A, "Test Guild")

    await service.mark_absent(GUILD_A)

    assert await service.is_present(GUILD_A) is False


async def test_reconcile(db_session: AsyncSession) -> None:
    service = BotGuildService()
    await service.mark_present(GUILD_A, "Stale Guild")

    await service.reconcile({GUILD_B: "Current Guild"})

    assert await service.present_guild_ids() == [GUILD_B]


async def test_get_name(db_session: AsyncSession) -> None:
    service = BotGuildService()
    await service.mark_present(GUILD_A, "Test Guild")

    assert await service.get_name(GUILD_A) == "Test Guild"
    assert await service.get_name(GUILD_B) is None
