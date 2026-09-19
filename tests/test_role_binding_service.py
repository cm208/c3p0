from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.role_binding import RoleBindingType
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.services.role_binding_service import RoleBindingService, RoleBindingValidationError

GUILD_A = 111


async def test_create_reaction_binding(db_session: AsyncSession) -> None:
    service = RoleBindingService()

    view = await service.create_reaction_binding(
        GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )

    assert view.interaction_type == RoleBindingType.REACTION
    assert view.emoji == "🎮"
    assert view.toggle is True
    assert view.enabled is True


async def test_create_reaction_binding_also_creates_parent_guild_config(
    db_session: AsyncSession,
) -> None:
    # RoleBinding.guild_id FKs to guild_config.guild_id, enforced against
    # the real sqlite file this runs against in production (not the
    # in-memory test database, which ignores FKs) - a guild the bot just
    # joined has no guild_config row yet, so this must create one rather
    # than assume it already exists.
    service = RoleBindingService(default_prefix="?")

    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_create_reaction_binding_rejects_duplicate_emoji(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)

    with pytest.raises(RoleBindingValidationError, match="already bound"):
        await service.create_reaction_binding(
            GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=4
        )


async def test_find_reaction_binding(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)

    found = await service.find_reaction_binding(GUILD_A, 100, "🎮")
    assert found is not None
    assert found.role_id == 3

    assert await service.find_reaction_binding(GUILD_A, 100, "🎵") is None


async def test_delete_binding_by_id(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    created = await service.create_reaction_binding(
        GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )

    deleted = await service.delete_binding(GUILD_A, created.id)

    assert deleted is True
    assert await service.get_binding(GUILD_A, created.id) is None


async def test_delete_binding_returns_false_for_unknown_id(db_session: AsyncSession) -> None:
    service = RoleBindingService()

    assert await service.delete_binding(GUILD_A, 999) is False


async def test_remove_reaction_binding(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)

    assert await service.remove_reaction_binding(GUILD_A, 100, "🎮") is True
    assert await service.remove_reaction_binding(GUILD_A, 100, "🎮") is False


async def test_create_button_binding(db_session: AsyncSession) -> None:
    service = RoleBindingService()

    view = await service.create_button_binding(
        GUILD_A, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:abc"
    )

    assert view.interaction_type == RoleBindingType.BUTTON
    assert view.component_custom_id == "c3p0:rolebtn:abc"

    found = await service.find_button_binding(GUILD_A, 100, "c3p0:rolebtn:abc")
    assert found is not None
    assert found.role_id == 3
    assert await service.find_button_binding(GUILD_A, 100, "nope") is None


async def test_create_button_binding_rejects_duplicate_role(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_button_binding(
        GUILD_A, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:a"
    )

    with pytest.raises(RoleBindingValidationError, match="already has a button"):
        await service.create_button_binding(
            GUILD_A, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:b"
        )


async def test_create_button_binding_rejects_over_limit(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    for i in range(25):
        await service.create_button_binding(
            GUILD_A, channel_id=1, message_id=100, role_id=i, custom_id=f"c3p0:rolebtn:{i}"
        )

    with pytest.raises(RoleBindingValidationError, match="maximum"):
        await service.create_button_binding(
            GUILD_A, channel_id=1, message_id=100, role_id=999, custom_id="c3p0:rolebtn:over"
        )


async def test_create_select_binding_uses_own_id_as_option_value(db_session: AsyncSession) -> None:
    service = RoleBindingService()

    view = await service.create_select_binding(GUILD_A, channel_id=1, message_id=100, role_id=3)

    assert view.interaction_type == RoleBindingType.SELECT
    assert view.component_custom_id == str(view.id)


async def test_create_select_binding_rejects_duplicate_role(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_select_binding(GUILD_A, channel_id=1, message_id=100, role_id=3)

    with pytest.raises(RoleBindingValidationError, match="already an option"):
        await service.create_select_binding(GUILD_A, channel_id=1, message_id=100, role_id=3)


async def test_list_for_message_filters_by_type(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)
    await service.create_button_binding(
        GUILD_A, channel_id=1, message_id=100, role_id=4, custom_id="c3p0:rolebtn:a"
    )

    all_for_message = await service.list_for_message(GUILD_A, 100)
    assert len(all_for_message) == 2

    buttons_only = await service.list_for_message(GUILD_A, 100, RoleBindingType.BUTTON)
    assert len(buttons_only) == 1
    assert buttons_only[0].role_id == 4


async def test_remove_binding_by_role(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_button_binding(
        GUILD_A, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:a"
    )

    assert await service.remove_binding_by_role(GUILD_A, 100, 3, RoleBindingType.BUTTON) is True
    assert await service.remove_binding_by_role(GUILD_A, 100, 3, RoleBindingType.BUTTON) is False


async def test_delete_message_bindings_by_type_leaves_other_types(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)
    await service.create_button_binding(
        GUILD_A, channel_id=1, message_id=100, role_id=4, custom_id="c3p0:rolebtn:a"
    )

    count = await service.delete_message_bindings_by_type(GUILD_A, 100, RoleBindingType.BUTTON)

    assert count == 1
    remaining = await service.list_for_message(GUILD_A, 100)
    assert len(remaining) == 1
    assert remaining[0].interaction_type == RoleBindingType.REACTION


async def test_delete_message_bindings(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎵", role_id=4)

    count = await service.delete_message_bindings(GUILD_A, 100)
    assert count == 2
    assert await service.list_bindings(GUILD_A) == []


async def test_set_message_enabled(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎵", role_id=4)

    count = await service.set_message_enabled(GUILD_A, 100, False)
    assert count == 2

    bindings = await service.list_bindings(GUILD_A)
    assert all(not b.enabled for b in bindings)


async def test_list_bindings_filters_by_type(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)

    reaction_only = await service.list_bindings(GUILD_A, RoleBindingType.REACTION)
    assert len(reaction_only) == 1

    button_only = await service.list_bindings(GUILD_A, RoleBindingType.BUTTON)
    assert button_only == []


async def test_list_message_locations(db_session: AsyncSession) -> None:
    service = RoleBindingService()
    await service.create_reaction_binding(GUILD_A, channel_id=1, message_id=100, emoji="🎮", role_id=3)
    await service.create_reaction_binding(GUILD_A, channel_id=2, message_id=200, emoji="🎵", role_id=4)

    locations = await service.list_message_locations(GUILD_A)
    assert set(locations) == {(1, 100), (2, 200)}
