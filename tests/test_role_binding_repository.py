from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.role_binding import RoleBindingType
from app.db.repositories.role_binding_repository import RoleBindingRepository

GUILD_A = 111
GUILD_B = 222


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)

    created = await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=2,
        role_id=3,
        emoji="🎮",
    )

    fetched = await repo.get(GUILD_A, created.id)
    assert fetched is not None
    assert fetched.emoji == "🎮"
    assert fetched.role_id == 3
    assert fetched.enabled is True
    assert fetched.toggle is True


async def test_get_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)

    created = await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=2,
        role_id=3,
        emoji="🎮",
    )

    # A different guild can't fetch another guild's binding by raw id.
    assert await repo.get(GUILD_B, created.id) is None


async def test_get_by_message_and_emoji(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)

    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )

    found = await repo.get_by_message_and_emoji(GUILD_A, 100, "🎮")
    assert found is not None
    assert found.role_id == 3

    assert await repo.get_by_message_and_emoji(GUILD_A, 100, "🎵") is None
    assert await repo.get_by_message_and_emoji(GUILD_B, 100, "🎮") is None


async def test_list_for_message_and_guild(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)

    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=4,
        emoji="🎵",
    )
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=200,
        role_id=5,
        emoji="📢",
    )

    for_message = await repo.list_for_message(GUILD_A, 100)
    assert {b.role_id for b in for_message} == {3, 4}

    for_guild = await repo.list_for_guild(GUILD_A)
    assert len(for_guild) == 3


async def test_set_enabled(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)
    created = await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )

    updated = await repo.set_enabled(GUILD_A, created.id, False)
    assert updated is not None
    assert updated.enabled is False

    assert await repo.set_enabled(GUILD_B, created.id, True) is None


async def test_delete(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)
    created = await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )

    assert await repo.delete(GUILD_B, created.id) is False  # wrong guild
    assert await repo.delete(GUILD_A, created.id) is True
    assert await repo.get(GUILD_A, created.id) is None


async def test_delete_for_message(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=4,
        emoji="🎵",
    )

    count = await repo.delete_for_message(GUILD_A, 100)
    assert count == 2
    assert await repo.list_for_message(GUILD_A, 100) == []


async def test_delete_for_message_and_type_leaves_other_types(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.BUTTON,
        source_channel_id=1,
        source_message_id=100,
        role_id=4,
        component_custom_id="c3p0:rolebtn:abc",
    )

    count = await repo.delete_for_message_and_type(GUILD_A, 100, RoleBindingType.BUTTON)

    assert count == 1
    remaining = await repo.list_for_message(GUILD_A, 100)
    assert len(remaining) == 1
    assert remaining[0].interaction_type == RoleBindingType.REACTION


async def test_all_message_locations_distinct(db_session: AsyncSession) -> None:
    repo = RoleBindingRepository(db_session)
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=3,
        emoji="🎮",
    )
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=1,
        source_message_id=100,
        role_id=4,
        emoji="🎵",
    )
    await repo.create(
        GUILD_A,
        interaction_type=RoleBindingType.REACTION,
        source_channel_id=2,
        source_message_id=200,
        role_id=5,
        emoji="📢",
    )

    locations = await repo.all_message_locations(GUILD_A)
    assert set(locations) == {(1, 100), (2, 200)}
