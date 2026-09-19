from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.music.guild_player import GuildPlayer
from app.services.music_service import MusicService, MusicValidationError

GUILD_A = 111


async def test_get_config_defaults(db_session: AsyncSession) -> None:
    service = MusicService()

    config = await service.get_config(GUILD_A)

    assert config.enabled is True
    assert config.default_volume == 50
    assert config.max_queue_size == 100


async def test_get_config_also_creates_parent_guild_config(db_session: AsyncSession) -> None:
    # MusicConfig.guild_id FKs to guild_config.guild_id, enforced against the
    # real sqlite file this runs against in production (not the in-memory
    # test database, which ignores FKs) - a guild the bot just joined has no
    # guild_config row yet, so this must create one rather than assume it
    # already exists.
    service = MusicService(default_prefix="?")

    await service.get_config(GUILD_A)

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_set_enabled(db_session: AsyncSession) -> None:
    # The repository method existed but had no service wiring at all - no
    # Discord command has ever been able to turn music off.
    service = MusicService()

    config = await service.set_enabled(GUILD_A, False)
    assert config.enabled is False

    config = await service.set_enabled(GUILD_A, True)
    assert config.enabled is True


async def test_set_default_volume_validates_range(db_session: AsyncSession) -> None:
    service = MusicService()

    with pytest.raises(MusicValidationError):
        await service.set_default_volume(GUILD_A, 101)
    with pytest.raises(MusicValidationError):
        await service.set_default_volume(GUILD_A, -1)

    updated = await service.set_default_volume(GUILD_A, 75)
    assert updated.default_volume == 75


async def test_set_max_queue_size_validates_range(db_session: AsyncSession) -> None:
    service = MusicService()

    with pytest.raises(MusicValidationError):
        await service.set_max_queue_size(GUILD_A, 0)
    with pytest.raises(MusicValidationError):
        await service.set_max_queue_size(GUILD_A, 100000)

    updated = await service.set_max_queue_size(GUILD_A, 10)
    assert updated.max_queue_size == 10


async def test_get_or_create_player_uses_config_defaults(db_session: AsyncSession) -> None:
    service = MusicService()
    await service.set_default_volume(GUILD_A, 30)
    await service.set_max_queue_size(GUILD_A, 5)

    player = await service.get_or_create_player(GUILD_A)

    assert isinstance(player, GuildPlayer)
    assert player.volume == pytest.approx(0.3)
    assert player.max_queue_size == 5


async def test_get_or_create_player_is_cached(db_session: AsyncSession) -> None:
    service = MusicService()

    first = await service.get_or_create_player(GUILD_A)
    second = await service.get_or_create_player(GUILD_A)

    assert first is second


async def test_remove_player(db_session: AsyncSession) -> None:
    service = MusicService()
    await service.get_or_create_player(GUILD_A)

    service.remove_player(GUILD_A)

    assert service.get_player(GUILD_A) is None
