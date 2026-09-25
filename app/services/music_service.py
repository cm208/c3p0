"""Music configuration service and per-guild player registry.

Config CRUD here follows the same plain-Python-view pattern as the other
services. get_or_create_player, by contrast, hands back a live GuildPlayer
(app/music/guild_player.py) - deliberately not discord-free, since a music
player is inherently tied to a discord.VoiceClient. See that module's
docstring for why this is a legitimate exception to the usual rule rather
than an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.music_config import MusicConfig
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.music_config_repository import MusicConfigRepository
from app.music.audio_provider import Track
from app.music.guild_player import GuildPlayer
from app.services.event_log_service import EventLogService, EventTag

MIN_VOLUME = 0
MAX_VOLUME = 100
MIN_QUEUE_SIZE = 1
MAX_QUEUE_SIZE = 1000


class MusicValidationError(Exception):
    """Raised for invalid music configuration input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class MusicConfigView:
    guild_id: int
    enabled: bool
    default_volume: int
    max_queue_size: int
    dj_role_id: int | None
    music_channel_id: int | None


def _to_view(config: MusicConfig) -> MusicConfigView:
    return MusicConfigView(
        guild_id=config.guild_id,
        enabled=config.enabled,
        default_volume=config.default_volume,
        max_queue_size=config.max_queue_size,
        dj_role_id=config.dj_role_id,
        music_channel_id=config.music_channel_id,
    )


class MusicService:
    def __init__(self, default_prefix: str = "!") -> None:
        self._players: dict[int, GuildPlayer] = {}
        self._events = EventLogService(default_prefix=default_prefix)
        # Only needed so that configuring music before ever running /config
        # still creates a GuildConfig row with the right default prefix,
        # rather than silently hardcoding "!" - see _ensure_guild_row.
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # MusicConfig.guild_id FKs to guild_config.guild_id, enforced (PRAGMA
        # foreign_keys=ON) against the real sqlite file this runs against in
        # production - unlike the in-memory test database, which silently
        # ignores FKs entirely. A guild the bot just joined has no
        # guild_config row yet (on_guild_join only touches bot_guild), so
        # get_or_create() below would otherwise raise an IntegrityError the
        # first time anything here is called before /config or a member
        # join ever runs for that guild.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    async def get_config(self, guild_id: int) -> MusicConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await MusicConfigRepository(session).get_or_create(guild_id)
            return _to_view(config)

    async def set_enabled(self, guild_id: int, enabled: bool) -> MusicConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await MusicConfigRepository(session).set_enabled(guild_id, enabled)
            return _to_view(config)

    async def set_default_volume(self, guild_id: int, volume: int) -> MusicConfigView:
        if not MIN_VOLUME <= volume <= MAX_VOLUME:
            raise MusicValidationError(f"Volume must be between {MIN_VOLUME} and {MAX_VOLUME}.")
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await MusicConfigRepository(session).set_default_volume(guild_id, volume)
            return _to_view(config)

    async def set_max_queue_size(self, guild_id: int, size: int) -> MusicConfigView:
        if not MIN_QUEUE_SIZE <= size <= MAX_QUEUE_SIZE:
            raise MusicValidationError(f"Queue size must be between {MIN_QUEUE_SIZE} and {MAX_QUEUE_SIZE}.")
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await MusicConfigRepository(session).set_max_queue_size(guild_id, size)
            return _to_view(config)

    async def set_dj_role(self, guild_id: int, role_id: int | None) -> MusicConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await MusicConfigRepository(session).set_dj_role(guild_id, role_id)
            return _to_view(config)

    async def set_music_channel(self, guild_id: int, channel_id: int | None) -> MusicConfigView:
        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            config = await MusicConfigRepository(session).set_music_channel(guild_id, channel_id)
            return _to_view(config)

    async def get_or_create_player(self, guild_id: int) -> GuildPlayer:
        player = self._players.get(guild_id)
        if player is not None:
            return player

        config = await self.get_config(guild_id)
        player = GuildPlayer(
            guild_id, max_queue_size=config.max_queue_size, default_volume=config.default_volume
        )
        player.on_track_start = lambda track: self._announce_track(guild_id, track)
        self._players[guild_id] = player
        return player

    async def _announce_track(self, guild_id: int, track: Track) -> None:
        await self._events.record(guild_id, EventTag.MUSIC, f"now playing: {track.title}")

    async def record_event(self, guild_id: int, text: str) -> None:
        """A MUSIC line in the dashboard's activity feed (queued, skipped, ...)."""
        await self._events.record(guild_id, EventTag.MUSIC, text)

    def get_player(self, guild_id: int) -> GuildPlayer | None:
        return self._players.get(guild_id)

    def remove_player(self, guild_id: int) -> None:
        self._players.pop(guild_id, None)
