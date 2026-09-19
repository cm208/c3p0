from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.guild_config_repository import GuildConfigRepository

GUILD_A = 111
GUILD_B = 222


async def test_get_returns_none_when_absent(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    assert await repo.get(GUILD_A) is None


async def test_get_or_create_is_idempotent(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    first = await repo.get_or_create(GUILD_A, default_prefix="!")
    second = await repo.get_or_create(GUILD_A, default_prefix="?")

    assert first.guild_id == second.guild_id
    # Prefix from the *first* creation wins; get_or_create shouldn't
    # silently overwrite existing config on a later call.
    assert second.prefix == "!"


async def test_set_prefix_persists(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    updated = await repo.set_prefix(GUILD_A, "?")

    assert updated.prefix == "?"
    reloaded = await repo.get(GUILD_A)
    assert reloaded is not None
    assert reloaded.prefix == "?"


async def test_guild_isolation(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    await repo.set_prefix(GUILD_A, "!")
    await repo.set_prefix(GUILD_B, "?")

    guild_a = await repo.get(GUILD_A)
    guild_b = await repo.get(GUILD_B)

    assert guild_a is not None and guild_a.prefix == "!"
    assert guild_b is not None and guild_b.prefix == "?"


async def test_set_default_role(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    updated = await repo.set_default_role(GUILD_A, 999)
    assert updated.default_role_id == 999

    cleared = await repo.set_default_role(GUILD_A, None)
    assert cleared.default_role_id is None


async def test_set_moderation_log_channel(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    updated = await repo.set_moderation_log_channel(GUILD_A, 777)
    assert updated.moderation_log_channel_id == 777

    cleared = await repo.set_moderation_log_channel(GUILD_A, None)
    assert cleared.moderation_log_channel_id is None


async def test_all_guild_ids(db_session: AsyncSession) -> None:
    repo = GuildConfigRepository(db_session)

    await repo.get_or_create(GUILD_A)
    await repo.get_or_create(GUILD_B)

    ids = await repo.all_guild_ids()
    assert set(ids) == {GUILD_A, GUILD_B}
