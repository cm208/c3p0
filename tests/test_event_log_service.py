from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.guild_event import GuildEvent
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.guild_event_repository import GuildEventRepository
from app.services import event_log_service
from app.services.event_log_service import EventLogService, EventTag

GUILD_A = 111
GUILD_B = 222


async def test_record_then_list_returns_oldest_first(db_session: AsyncSession) -> None:
    service = EventLogService()
    await service.record(GUILD_A, EventTag.JOIN, "@alice joined")
    await service.record(GUILD_A, EventTag.CMD, "!rules invoked by @alice")

    events = await service.list_after(GUILD_A)

    assert [(e.tag, e.text) for e in events] == [("JOIN", "@alice joined"), ("CMD", "!rules invoked by @alice")]


async def test_record_creates_the_parent_guild_config_row(db_session: AsyncSession) -> None:
    await EventLogService().record(GUILD_A, EventTag.JOIN, "@alice joined")

    async with session_scope() as session:
        assert await GuildConfigRepository(session).get(GUILD_A) is not None


async def test_list_after_only_returns_newer_events_for_that_guild(db_session: AsyncSession) -> None:
    service = EventLogService()
    await service.record(GUILD_A, EventTag.JOIN, "first")
    first_id = (await service.list_after(GUILD_A))[0].id
    await service.record(GUILD_A, EventTag.LEAVE, "second")
    await service.record(GUILD_B, EventTag.JOIN, "other guild")

    events = await service.list_after(GUILD_A, after_id=first_id)

    assert [e.text for e in events] == ["second"]


async def test_list_after_zero_returns_the_newest_window(db_session: AsyncSession) -> None:
    service = EventLogService()
    for i in range(5):
        await service.record(GUILD_A, EventTag.CMD, f"event {i}")

    events = await service.list_after(GUILD_A, limit=2)

    assert [e.text for e in events] == ["event 3", "event 4"]


async def test_record_collapses_whitespace_and_truncates(db_session: AsyncSession) -> None:
    await EventLogService().record(GUILD_A, EventTag.MOD, "warn  @bob\n" + "x" * 500)

    (event,) = await EventLogService().list_after(GUILD_A)
    assert event.text.startswith("warn @bob x")
    assert len(event.text) == event_log_service.MAX_TEXT_LENGTH


async def test_record_never_raises(db_session: AsyncSession, monkeypatch) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(GuildEventRepository, "create", broken)

    await EventLogService().record(GUILD_A, EventTag.JOIN, "@alice joined")  # must not raise


async def test_prune_keeps_only_the_newest_rows(db_session: AsyncSession) -> None:
    service = EventLogService()
    for i in range(6):
        await service.record(GUILD_A, EventTag.CMD, f"event {i}")
    await service.record(GUILD_B, EventTag.CMD, "untouched")

    async with session_scope() as session:
        await GuildEventRepository(session).prune(GUILD_A, keep=2)

    assert [e.text for e in await service.list_after(GUILD_A)] == ["event 4", "event 5"]
    assert [e.text for e in await service.list_after(GUILD_B)] == ["untouched"]


async def test_net_joins_counts_joins_minus_leaves_in_the_window(db_session: AsyncSession) -> None:
    service = EventLogService()
    for _ in range(3):
        await service.record(GUILD_A, EventTag.JOIN, "joined")
    await service.record(GUILD_A, EventTag.LEAVE, "left")
    # An old join outside the 7-day window doesn't count.
    async with session_scope() as session:
        session.add(
            GuildEvent(
                guild_id=GUILD_A, tag=EventTag.JOIN, text="old", created_at=datetime.now(UTC) - timedelta(days=9)
            )
        )

    assert await service.net_joins_since(GUILD_A) == 2
