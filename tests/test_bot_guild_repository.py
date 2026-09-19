from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.bot_guild_repository import BotGuildRepository

GUILD_A = 111
GUILD_B = 222


async def test_is_present_false_when_unknown(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    assert await repo.is_present(GUILD_A) is False


async def test_get_name_none_when_unknown(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    assert await repo.get_name(GUILD_A) is None


async def test_get_name_returns_stored_name(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)
    await repo.mark_present(GUILD_A, "Witchy Bitchy")

    assert await repo.get_name(GUILD_A) == "Witchy Bitchy"


async def test_get_name_still_returns_name_after_going_absent(db_session: AsyncSession) -> None:
    # A guild the bot left should still resolve a name for display purposes
    # (e.g. an old infraction log entry) even though is_present is now False.
    repo = BotGuildRepository(db_session)
    await repo.mark_present(GUILD_A, "Witchy Bitchy")
    await repo.mark_absent(GUILD_A)

    assert await repo.get_name(GUILD_A) == "Witchy Bitchy"


async def test_mark_present_then_absent(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    await repo.mark_present(GUILD_A, "Test Guild")
    assert await repo.is_present(GUILD_A) is True

    await repo.mark_absent(GUILD_A)
    assert await repo.is_present(GUILD_A) is False


async def test_mark_absent_on_unknown_guild_is_a_noop(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    await repo.mark_absent(GUILD_A)  # should not raise

    assert await repo.is_present(GUILD_A) is False


async def test_mark_present_updates_name_and_rejoin(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    await repo.mark_present(GUILD_A, "Old Name")
    await repo.mark_absent(GUILD_A)
    await repo.mark_present(GUILD_A, "New Name")

    assert await repo.is_present(GUILD_A) is True


async def test_present_guild_ids_excludes_absent(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    await repo.mark_present(GUILD_A, "A")
    await repo.mark_present(GUILD_B, "B")
    await repo.mark_absent(GUILD_B)

    assert await repo.present_guild_ids() == [GUILD_A]


async def test_reconcile_adds_and_removes(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    # Guild A was present from a previous session; guild B is new.
    await repo.mark_present(GUILD_A, "A")

    await repo.reconcile({GUILD_B: "B"})

    assert await repo.is_present(GUILD_A) is False
    assert await repo.is_present(GUILD_B) is True


async def test_reconcile_keeps_still_present_guilds(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    await repo.reconcile({GUILD_A: "A", GUILD_B: "B"})
    await repo.reconcile({GUILD_A: "A", GUILD_B: "B"})

    assert set(await repo.present_guild_ids()) == {GUILD_A, GUILD_B}


async def test_present_guilds_excludes_absent(db_session: AsyncSession) -> None:
    repo = BotGuildRepository(db_session)

    await repo.mark_present(GUILD_A, "Guild A")
    await repo.mark_present(GUILD_B, "Guild B")
    await repo.mark_absent(GUILD_B)

    assert await repo.present_guilds() == {GUILD_A: "Guild A"}
