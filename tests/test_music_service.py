from __future__ import annotations

import asyncio

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


# --- Idle disconnect ---


class FakeVoiceClient:
    def __init__(self) -> None:
        self.playing = False
        self.disconnected = False

    def is_connected(self) -> bool:
        return not self.disconnected

    def is_playing(self) -> bool:
        return self.playing

    def is_paused(self) -> bool:
        return False

    async def disconnect(self, force: bool = False) -> None:
        self.disconnected = True


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Makes the idle timer's sleep instant, recording what it was asked for."""
    real_sleep = asyncio.sleep
    requested: list[float] = []

    async def instant(delay: float) -> None:
        requested.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("app.services.music_service.asyncio.sleep", instant)
    return requested


async def _connected_player(service: MusicService) -> tuple[GuildPlayer, FakeVoiceClient]:
    player = await service.get_or_create_player(GUILD_A)
    vc = FakeVoiceClient()
    player.voice_client = vc  # type: ignore[assignment]
    return player, vc


async def _run_idle_timer(service: MusicService) -> None:
    task = service._idle_tasks.get(GUILD_A)
    assert task is not None
    await task


async def test_set_idle_disconnect_minutes_validates_range(db_session: AsyncSession) -> None:
    service = MusicService()

    assert (await service.get_config(GUILD_A)).idle_disconnect_minutes == 5
    assert (await service.set_idle_disconnect_minutes(GUILD_A, 0)).idle_disconnect_minutes == 0
    with pytest.raises(MusicValidationError):
        await service.set_idle_disconnect_minutes(GUILD_A, -1)
    with pytest.raises(MusicValidationError):
        await service.set_idle_disconnect_minutes(GUILD_A, 121)


async def test_idle_player_leaves_after_configured_minutes(
    db_session: AsyncSession, fast_sleep: list[float]
) -> None:
    service = MusicService()
    await service.set_idle_disconnect_minutes(GUILD_A, 7)
    player, vc = await _connected_player(service)

    await player.on_idle()  # type: ignore[misc]
    await _run_idle_timer(service)

    assert fast_sleep == [7 * 60]
    assert vc.disconnected
    assert service.get_player(GUILD_A) is None
    assert GUILD_A not in service._idle_tasks


async def test_idle_timer_does_nothing_while_playing(
    db_session: AsyncSession, fast_sleep: list[float]
) -> None:
    service = MusicService()
    player, vc = await _connected_player(service)
    vc.playing = True

    await player.on_idle()  # type: ignore[misc]
    await _run_idle_timer(service)

    assert not vc.disconnected
    assert service.get_player(GUILD_A) is player


async def test_zero_minutes_never_leaves(db_session: AsyncSession) -> None:
    service = MusicService()
    await service.set_idle_disconnect_minutes(GUILD_A, 0)
    player, _vc = await _connected_player(service)

    await player.on_idle()  # type: ignore[misc]

    assert GUILD_A not in service._idle_tasks


async def test_rearming_replaces_the_pending_timer(db_session: AsyncSession) -> None:
    service = MusicService()
    player, _vc = await _connected_player(service)

    await player.on_idle()  # type: ignore[misc]
    first = service._idle_tasks[GUILD_A]
    await player.on_idle()  # type: ignore[misc]
    second = service._idle_tasks[GUILD_A]

    assert second is not first
    with pytest.raises(asyncio.CancelledError):
        await first
    service.remove_player(GUILD_A)
    with pytest.raises(asyncio.CancelledError):
        await second


async def test_remove_player_cancels_idle_timer(db_session: AsyncSession) -> None:
    service = MusicService()
    player, _vc = await _connected_player(service)
    await player.on_idle()  # type: ignore[misc]
    task = service._idle_tasks[GUILD_A]

    service.remove_player(GUILD_A)

    with pytest.raises(asyncio.CancelledError):
        await task
