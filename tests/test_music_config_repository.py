from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.music_config_repository import MusicConfigRepository

GUILD_A = 111


async def test_get_or_create_defaults(db_session: AsyncSession) -> None:
    repo = MusicConfigRepository(db_session)

    config = await repo.get_or_create(GUILD_A)

    assert config.guild_id == GUILD_A
    assert config.enabled is True
    assert config.default_volume == 50
    assert config.max_queue_size == 100
    assert config.dj_role_id is None


async def test_set_default_volume(db_session: AsyncSession) -> None:
    repo = MusicConfigRepository(db_session)

    updated = await repo.set_default_volume(GUILD_A, 80)

    assert updated.default_volume == 80


async def test_set_max_queue_size(db_session: AsyncSession) -> None:
    repo = MusicConfigRepository(db_session)

    updated = await repo.set_max_queue_size(GUILD_A, 25)

    assert updated.max_queue_size == 25


async def test_set_dj_role(db_session: AsyncSession) -> None:
    repo = MusicConfigRepository(db_session)

    updated = await repo.set_dj_role(GUILD_A, 999)
    assert updated.dj_role_id == 999

    cleared = await repo.set_dj_role(GUILD_A, None)
    assert cleared.dj_role_id is None


async def test_set_music_channel(db_session: AsyncSession) -> None:
    repo = MusicConfigRepository(db_session)

    updated = await repo.set_music_channel(GUILD_A, 555)

    assert updated.music_channel_id == 555


async def test_set_enabled(db_session: AsyncSession) -> None:
    repo = MusicConfigRepository(db_session)

    updated = await repo.set_enabled(GUILD_A, False)

    assert updated.enabled is False
