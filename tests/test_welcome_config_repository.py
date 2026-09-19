from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.welcome_config_repository import WelcomeConfigRepository

GUILD_A = 111
GUILD_B = 222


async def test_get_returns_none_when_absent(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    assert await repo.get(GUILD_A) is None


async def test_get_or_create_defaults(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    config = await repo.get_or_create(GUILD_A)

    assert config.guild_id == GUILD_A
    assert config.enabled is False
    assert config.message_enabled is True
    assert config.embed_enabled is False
    assert config.dm_enabled is False
    assert config.role_enabled is False
    assert config.join_log_enabled is False


async def test_get_or_create_is_idempotent(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    first = await repo.get_or_create(GUILD_A)
    await repo.set_enabled(GUILD_A, True)
    second = await repo.get_or_create(GUILD_A)

    assert first.guild_id == second.guild_id
    assert second.enabled is True


async def test_set_channel_persists(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    await repo.set_channel(GUILD_A, 555)
    reloaded = await repo.get(GUILD_A)

    assert reloaded is not None
    assert reloaded.channel_id == 555


async def test_set_message_template(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    updated = await repo.set_message_template(GUILD_A, "Hi {user}")

    assert updated.message_template == "Hi {user}"


async def test_set_role_enables_it(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    updated = await repo.set_role(GUILD_A, 999)

    assert updated.role_id == 999
    assert updated.role_enabled is True


async def test_disable_role_keeps_role_id(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    await repo.set_role(GUILD_A, 999)
    disabled = await repo.disable_role(GUILD_A)

    assert disabled.role_id == 999
    assert disabled.role_enabled is False


async def test_embed_field_setters(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    await repo.set_embed_enabled(GUILD_A, True)
    await repo.set_embed_title(GUILD_A, "Hello")
    await repo.set_embed_description(GUILD_A, "Description {user}")
    updated = await repo.set_embed_footer(GUILD_A, "Footer")

    assert updated.embed_enabled is True
    assert updated.embed_title == "Hello"
    assert updated.embed_description == "Description {user}"
    assert updated.embed_footer == "Footer"


async def test_set_join_log_enabled(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    updated = await repo.set_join_log_enabled(GUILD_A, True)

    assert updated.join_log_enabled is True


async def test_reset_restores_defaults(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    await repo.set_enabled(GUILD_A, True)
    await repo.set_channel(GUILD_A, 555)
    await repo.set_message_template(GUILD_A, "Hi {user}")
    await repo.set_role(GUILD_A, 999)
    await repo.set_join_log_enabled(GUILD_A, True)

    reset = await repo.reset(GUILD_A)

    assert reset.enabled is False
    assert reset.channel_id is None
    assert reset.message_template is None
    assert reset.role_enabled is False
    assert reset.role_id is None
    assert reset.join_log_enabled is False


async def test_guild_isolation(db_session: AsyncSession) -> None:
    repo = WelcomeConfigRepository(db_session)

    await repo.set_channel(GUILD_A, 111)
    await repo.set_channel(GUILD_B, 222)

    guild_a = await repo.get(GUILD_A)
    guild_b = await repo.get(GUILD_B)

    assert guild_a is not None and guild_a.channel_id == 111
    assert guild_b is not None and guild_b.channel_id == 222
