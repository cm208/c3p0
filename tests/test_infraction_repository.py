from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.infraction import InfractionType
from app.db.repositories.infraction_repository import InfractionRepository

GUILD_A = 111
GUILD_B = 222
USER_A = 1001


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)

    created = await repo.create(
        GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN, reason="spam"
    )

    fetched = await repo.get(GUILD_A, created.id)
    assert fetched is not None
    assert fetched.reason == "spam"
    assert fetched.active is True


async def test_get_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    created = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    assert await repo.get(GUILD_B, created.id) is None


async def test_list_for_user_orders_newest_first(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    first = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    second = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.KICK)

    infractions = await repo.list_for_user(GUILD_A, USER_A)

    assert [i.id for i in infractions] == [second.id, first.id]


async def test_list_for_guild_spans_all_users_ordered_newest_first(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    first = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    second = await repo.create(GUILD_A, user_id=9999, moderator_id=2, type=InfractionType.KICK)

    infractions = await repo.list_for_guild(GUILD_A)

    assert [i.id for i in infractions] == [second.id, first.id]


async def test_list_for_guild_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    await repo.create(GUILD_B, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    infractions = await repo.list_for_guild(GUILD_A)

    assert len(infractions) == 1
    assert infractions[0].guild_id == GUILD_A


async def test_count_for_guild_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.KICK)
    await repo.create(GUILD_B, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    assert await repo.count_for_guild(GUILD_A) == 2
    assert await repo.count_for_guild(GUILD_B) == 1


async def test_count_for_guild_zero_when_none(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)

    assert await repo.count_for_guild(GUILD_A) == 0


async def test_list_for_guild_respects_limit_and_offset(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    created = [
        await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
        for _ in range(5)
    ]

    page = await repo.list_for_guild(GUILD_A, limit=2, offset=1)

    # Newest-first overall order is created[4], created[3], created[2], ...
    # offset=1 skips created[4], limit=2 takes the next two.
    assert [i.id for i in page] == [created[3].id, created[2].id]


async def test_list_active_for_user_excludes_resolved(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    active = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    resolved = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    await repo.set_active(GUILD_A, resolved.id, False)

    infractions = await repo.list_active_for_user(GUILD_A, USER_A)

    assert [i.id for i in infractions] == [active.id]


async def test_count_active_by_type(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    kick = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.KICK)
    resolved_warn = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    await repo.set_active(GUILD_A, resolved_warn.id, False)

    assert await repo.count_active_by_type(GUILD_A, USER_A, InfractionType.WARN) == 2
    assert await repo.count_active_by_type(GUILD_A, USER_A, InfractionType.KICK) == 1
    assert kick is not None


async def test_set_active_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    created = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    assert await repo.set_active(GUILD_B, created.id, False) is None
    updated = await repo.set_active(GUILD_A, created.id, False)
    assert updated is not None
    assert updated.active is False


async def test_resolve_active_by_type(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.BAN)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    count = await repo.resolve_active_by_type(GUILD_A, USER_A, InfractionType.BAN)

    assert count == 1
    assert await repo.count_active_by_type(GUILD_A, USER_A, InfractionType.BAN) == 0
    assert await repo.count_active_by_type(GUILD_A, USER_A, InfractionType.WARN) == 1


async def test_update_reason_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    created = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN, reason="old")

    assert await repo.update_reason(GUILD_B, created.id, "hijacked") is None
    updated = await repo.update_reason(GUILD_A, created.id, "corrected reason")
    assert updated is not None
    assert updated.reason == "corrected reason"


async def test_delete_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    created = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    assert await repo.delete(GUILD_B, created.id) is False
    assert await repo.delete(GUILD_A, created.id) is True
    assert await repo.get(GUILD_A, created.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)

    assert await repo.delete(GUILD_A, 999999) is False


async def test_search_for_guild_filters_by_text_against_reason_and_ids(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    spam = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN, reason="Spamming links")
    other = await repo.create(GUILD_A, user_id=9999, moderator_id=2, type=InfractionType.WARN, reason="Being rude")

    by_reason = await repo.search_for_guild(GUILD_A, text="spam")
    assert [i.id for i in by_reason] == [spam.id]

    by_id = await repo.search_for_guild(GUILD_A, text=str(USER_A))
    assert [i.id for i in by_id] == [spam.id]

    assert other is not None


async def test_search_for_guild_filters_by_type_and_active(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    warn = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)
    kick = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.KICK)
    await repo.set_active(GUILD_A, warn.id, False)

    by_type = await repo.search_for_guild(GUILD_A, type=InfractionType.KICK)
    assert [i.id for i in by_type] == [kick.id]

    resolved_only = await repo.search_for_guild(GUILD_A, active=False)
    assert [i.id for i in resolved_only] == [warn.id]


async def test_search_for_guild_sorts_and_falls_back_to_created_at(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    ban = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.BAN)
    warn = await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN)

    ascending_by_type = await repo.search_for_guild(GUILD_A, sort="type", direction="asc")
    assert [i.id for i in ascending_by_type] == sorted([ban.id, warn.id], key=lambda i: "ban" if i == ban.id else "warn")

    # An unrecognized sort column degrades to created_at instead of erroring.
    default_order = await repo.search_for_guild(GUILD_A, sort="not-a-real-column")
    assert [i.id for i in default_order] == [warn.id, ban.id]


async def test_count_search_for_guild_matches_search_for_guild_filters(db_session: AsyncSession) -> None:
    repo = InfractionRepository(db_session)
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.WARN, reason="Spamming")
    await repo.create(GUILD_A, user_id=USER_A, moderator_id=2, type=InfractionType.KICK, reason="Unrelated")

    assert await repo.count_search_for_guild(GUILD_A, text="spam") == 1
    assert await repo.count_search_for_guild(GUILD_A) == 2
