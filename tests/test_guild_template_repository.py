from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.guild_template_repository import GuildTemplateRepository

GUILD_A = 111
GUILD_B = 222

_DEFINITION = {"roles": [], "channels": []}


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = GuildTemplateRepository(db_session)

    created = await repo.create(
        GUILD_A, name="My Layout", description="Saved from live state", definition=_DEFINITION, created_by=1
    )

    fetched = await repo.get(GUILD_A, created.id)
    assert fetched is not None
    assert fetched.name == "My Layout"
    assert fetched.definition == _DEFINITION


async def test_get_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = GuildTemplateRepository(db_session)
    created = await repo.create(GUILD_A, name="My Layout", description=None, definition=_DEFINITION, created_by=1)

    assert await repo.get(GUILD_B, created.id) is None


async def test_list_for_guild_orders_by_name_and_is_scoped(db_session: AsyncSession) -> None:
    repo = GuildTemplateRepository(db_session)
    await repo.create(GUILD_A, name="Zebra", description=None, definition=_DEFINITION, created_by=1)
    await repo.create(GUILD_A, name="Alpha", description=None, definition=_DEFINITION, created_by=1)
    await repo.create(GUILD_B, name="Other Guild's", description=None, definition=_DEFINITION, created_by=1)

    templates = await repo.list_for_guild(GUILD_A)

    assert [t.name for t in templates] == ["Alpha", "Zebra"]


async def test_delete_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = GuildTemplateRepository(db_session)
    created = await repo.create(GUILD_A, name="My Layout", description=None, definition=_DEFINITION, created_by=1)

    assert await repo.delete(GUILD_B, created.id) is False
    assert await repo.delete(GUILD_A, created.id) is True
    assert await repo.get(GUILD_A, created.id) is None
