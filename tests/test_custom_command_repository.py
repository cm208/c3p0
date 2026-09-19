from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.custom_command_repository import CustomCommandRepository

GUILD_A = 111
GUILD_B = 222


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)

    created = await repo.create(
        GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1
    )

    fetched = await repo.get(GUILD_A, created.id)
    assert fetched is not None
    assert fetched.trigger == "!rules"
    assert fetched.enabled is True
    assert fetched.restriction_type == "public"
    assert fetched.cooldown_type == "none"


async def test_get_scoped_to_guild(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    created = await repo.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    assert await repo.get(GUILD_B, created.id) is None


async def test_get_by_trigger_scoped_per_guild(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    await repo.create(GUILD_A, name="Rules", trigger="!rules", response="A", created_by=1)
    await repo.create(GUILD_B, name="Rules", trigger="!rules", response="B", created_by=1)

    a = await repo.get_by_trigger(GUILD_A, "!rules")
    b = await repo.get_by_trigger(GUILD_B, "!rules")

    assert a is not None and a.response == "A"
    assert b is not None and b.response == "B"
    assert await repo.get_by_trigger(GUILD_A, "!missing") is None


async def test_list_for_guild_orders_by_name(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    await repo.create(GUILD_A, name="Zebra", trigger="!z", response="z", created_by=1)
    await repo.create(GUILD_A, name="Alpha", trigger="!a", response="a", created_by=1)

    commands = await repo.list_for_guild(GUILD_A)

    assert [c.name for c in commands] == ["Alpha", "Zebra"]


async def test_delete(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    created = await repo.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    assert await repo.delete(GUILD_B, created.id) is False
    assert await repo.delete(GUILD_A, created.id) is True
    assert await repo.get(GUILD_A, created.id) is None


async def test_set_enabled(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    created = await repo.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    updated = await repo.set_enabled(GUILD_A, created.id, False)
    assert updated is not None
    assert updated.enabled is False


async def test_set_response(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    created = await repo.create(GUILD_A, name="Rules", trigger="!rules", response="Old", created_by=1)

    updated = await repo.set_response(GUILD_A, created.id, "New")
    assert updated is not None
    assert updated.response == "New"


async def test_set_restriction(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    created = await repo.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    updated = await repo.set_restriction(
        GUILD_A, created.id, restriction_type="role", restricted_role_id=999, restricted_permission=None
    )
    assert updated is not None
    assert updated.restriction_type == "role"
    assert updated.restricted_role_id == 999


async def test_set_cooldown(db_session: AsyncSession) -> None:
    repo = CustomCommandRepository(db_session)
    created = await repo.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    updated = await repo.set_cooldown(GUILD_A, created.id, cooldown_type="user", cooldown_seconds=30)
    assert updated is not None
    assert updated.cooldown_type == "user"
    assert updated.cooldown_seconds == 30
