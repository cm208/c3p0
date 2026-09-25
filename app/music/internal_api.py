"""Internal control-plane API for live music state/controls.

Runs INSIDE the bot process (see app/bot.py's setup_hook), on the bot's own
event loop, listening on a Compose-network-internal-only port - never
published to the host, see compose.yaml. The only client is c3p0-web
(app/web/bot_client.py), authenticated with a shared-secret bearer token
(INTERNAL_API_TOKEN) rather than any user identity: the actual
DJ-equivalent permission check already happened one hop earlier, when the
caller loaded a guild's dashboard page at all (require_guild_access already
requires Manage Guild, which MusicCog._check_dj already treats as an
automatic pass - see app/web/routers/music.py).

Depends on MusicService, never MusicCog, to keep this module out of
app/cogs/ entirely (cogs depend on services, not the reverse). app/bot.py
is the only place that reaches into
bot.get_cog("Music") to extract the one live MusicService instance and
hand it in here - every route handler below mutates that same instance's
GuildPlayers, so nothing needs polling or thread-hopping.

GET /status is the one non-music route: bot-wide health for the
dashboard's status bar (version, gateway latency, uptime). It lives here
because this is already the web process's only channel into live
bot-process state - see CLAUDE.md's "internal control-plane API" section.
"""

from __future__ import annotations

import hmac
import logging
import math
import time
from typing import Annotated

import discord
from discord.ext import commands
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import __version__
from app.metrics import MUSIC_FAILURES, MUSIC_PLAYS, MUSIC_QUEUE_LENGTH
from app.music.audio_provider import AudioResolutionError, Track
from app.music.guild_player import GuildPlayer, QueueFullError
from app.services.music_service import MusicConfigView, MusicService

logger = logging.getLogger(__name__)


class TrackOut(BaseModel):
    title: str
    webpage_url: str
    duration_seconds: int | None
    requested_by: int
    requested_by_name: str


class PlayerStateOut(BaseModel):
    connected: bool
    voice_channel_id: int | None
    voice_channel_name: str | None = None
    playing: bool
    paused: bool
    current: TrackOut | None
    elapsed_seconds: float | None
    volume_percent: int
    loop_mode: str
    queue: list[TrackOut]
    max_queue_size: int

    @classmethod
    def from_player(cls, player: GuildPlayer | None, guild: discord.Guild) -> PlayerStateOut:
        if player is None:
            return cls(
                connected=False,
                voice_channel_id=None,
                playing=False,
                paused=False,
                current=None,
                elapsed_seconds=None,
                volume_percent=0,
                loop_mode="off",
                queue=[],
                max_queue_size=0,
            )
        connected = player.is_connected()
        voice_client = player.voice_client
        return cls(
            connected=connected,
            voice_channel_id=voice_client.channel.id if connected else None,
            voice_channel_name=voice_client.channel.name if connected else None,
            playing=connected and voice_client.is_playing(),
            paused=connected and voice_client.is_paused(),
            current=_track_out(player.current, guild) if player.current is not None else None,
            elapsed_seconds=player.elapsed_seconds,
            volume_percent=round(player.volume * 100),
            loop_mode=player.loop_mode.value,
            queue=[_track_out(track, guild) for track in player.queue],
            max_queue_size=player.max_queue_size,
        )


class BotStatusOut(BaseModel):
    version: str
    ready: bool
    # None until the first heartbeat ACK (discord.py reports inf/nan then).
    latency_ms: int | None
    shard_id: int
    shard_count: int
    guild_count: int
    started_at: str | None
    uptime_seconds: int | None


class VoiceChannelOut(BaseModel):
    id: int
    name: str


class EnqueueRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    requested_by: int
    voice_channel_id: int | None = None


class VolumeRequest(BaseModel):
    percent: int = Field(ge=0, le=100)


class EnqueueResponse(BaseModel):
    track: TrackOut
    position: int
    started: bool
    state: PlayerStateOut


def _track_out(track: Track, guild: discord.Guild) -> TrackOut:
    # Resolved from the bot's own already-live member cache (guild.members
    # intent is required and already enabled - see app/bot.py's
    # REQUIRED_INTENTS) rather than a REST call, so this is free even when
    # called once per queue row on every poll tick.
    member = guild.get_member(track.requested_by)
    name = member.display_name if member is not None else f"User {track.requested_by}"
    return TrackOut(
        title=track.title,
        webpage_url=track.webpage_url,
        duration_seconds=track.duration_seconds,
        requested_by=track.requested_by,
        requested_by_name=name,
    )


async def _announce_web_enqueue(
    guild: discord.Guild, config: MusicConfigView, track_out: TrackOut, *, position: int, started: bool
) -> None:
    """Post a message into the guild's configured music-activity channel
    when a track was queued from the web dashboard - the one origin
    MusicCog.play's own ctx.send(...) confirmation can never cover, since a
    dashboard "Add to Queue" never touches a Discord command context at
    all. Silent no-op if music_channel_id isn't set (the default) - no
    guessed fallback channel, same "off means off" behavior every other
    optional channel setting on this dashboard already has. Best-effort:
    a channel the bot can no longer post in (permissions changed, channel
    deleted) must never fail the enqueue response itself, since the track
    was already successfully queued by the time this runs.
    """
    if config.music_channel_id is None:
        return
    channel = guild.get_channel(config.music_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    if started:
        message = f"▶️ **{track_out.title}** started playing — added from the dashboard by {track_out.requested_by_name}."
    else:
        message = (
            f"➕ **{track_out.title}** was added to the queue (position {position}) "
            f"— added from the dashboard by {track_out.requested_by_name}."
        )
    try:
        await channel.send(message)
    except discord.HTTPException:
        logger.warning(
            "Couldn't post the web-queue announcement", extra={"guild_id": guild.id, "channel_id": channel.id}
        )


async def _require_internal_token(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    expected = request.app.state.internal_api_token
    provided = authorization[len("Bearer ") :] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing internal API token.")


def _get_guild(request: Request, guild_id: int) -> discord.Guild:
    guild = request.app.state.bot.get_guild(guild_id)
    if guild is None:
        raise HTTPException(status_code=404, detail="Bot is not in that guild.")
    return guild


def _get_service(request: Request) -> MusicService:
    return request.app.state.music_service


def create_internal_app(bot: commands.Bot, *, music_service: MusicService, internal_api_token: str) -> FastAPI:
    app = FastAPI(title="C3P0 Internal Music API", dependencies=[Depends(_require_internal_token)])
    app.state.bot = bot
    app.state.music_service = music_service
    app.state.internal_api_token = internal_api_token

    @app.get("/status", response_model=BotStatusOut)
    async def get_status(request: Request) -> BotStatusOut:
        bot = request.app.state.bot
        latency = bot.latency
        started_at = getattr(bot, "started_at", None)
        started_monotonic = getattr(bot, "started_monotonic", None)
        return BotStatusOut(
            version=__version__,
            ready=bot.is_ready(),
            latency_ms=round(latency * 1000) if math.isfinite(latency) else None,
            shard_id=bot.shard_id or 0,
            shard_count=bot.shard_count or 1,
            guild_count=len(bot.guilds),
            started_at=started_at.isoformat() if started_at is not None else None,
            uptime_seconds=int(time.monotonic() - started_monotonic) if started_monotonic is not None else None,
        )

    @app.get("/guilds/{guild_id}/music/state", response_model=PlayerStateOut)
    async def get_state(guild_id: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        return PlayerStateOut.from_player(player, guild)

    @app.get("/guilds/{guild_id}/music/voice-channels", response_model=list[VoiceChannelOut])
    async def list_voice_channels(guild_id: int, request: Request) -> list[VoiceChannelOut]:
        guild = _get_guild(request, guild_id)
        me = guild.me
        if me is None:
            return []
        return [
            VoiceChannelOut(id=channel.id, name=channel.name)
            for channel in guild.voice_channels
            if channel.permissions_for(me).connect
        ]

    @app.post("/guilds/{guild_id}/music/enqueue", response_model=EnqueueResponse)
    async def enqueue(guild_id: int, body: EnqueueRequest, request: Request) -> EnqueueResponse:
        guild = _get_guild(request, guild_id)
        service = _get_service(request)

        config = await service.get_config(guild_id)
        if not config.enabled:
            raise HTTPException(status_code=403, detail="Music is disabled on this server.")

        player = await service.get_or_create_player(guild_id)
        if not player.is_connected():
            if body.voice_channel_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="Not connected to a voice channel yet - choose one to start playback.",
                )
            channel = guild.get_channel(body.voice_channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                raise HTTPException(status_code=404, detail="Voice channel not found.")
            try:
                await player.connect(channel)
            except (discord.ClientException, discord.Forbidden) as exc:
                raise HTTPException(status_code=502, detail=f"Couldn't join that channel ({exc}).") from exc

        try:
            track, position = await player.resolve_and_enqueue(body.query, requested_by=body.requested_by)
        except AudioResolutionError as exc:
            MUSIC_FAILURES.labels(reason="resolve_error").inc()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except QueueFullError as exc:
            MUSIC_FAILURES.labels(reason="queue_full").inc()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        MUSIC_QUEUE_LENGTH.labels(guild_id=str(guild_id)).set(len(player.queue))

        started = False
        if not player.is_playing():
            try:
                started = await player.start_or_advance()
            except discord.ClientException as exc:
                MUSIC_FAILURES.labels(reason="playback_error").inc()
                raise HTTPException(status_code=502, detail=f"Couldn't start playback ({exc}).") from exc
            if started:
                MUSIC_PLAYS.inc()

        track_out = _track_out(track, guild)
        await _announce_web_enqueue(guild, config, track_out, position=position, started=started)
        await service.record_event(guild_id, f'queued "{track.title}" by @{track_out.requested_by_name} (dashboard)')

        return EnqueueResponse(
            track=track_out,
            position=position,
            started=started,
            state=PlayerStateOut.from_player(player, guild),
        )

    @app.post("/guilds/{guild_id}/music/pause", response_model=PlayerStateOut)
    async def pause(guild_id: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        if player is None or not player.pause():
            raise HTTPException(status_code=404, detail="Nothing is playing.")
        return PlayerStateOut.from_player(player, guild)

    @app.post("/guilds/{guild_id}/music/resume", response_model=PlayerStateOut)
    async def resume(guild_id: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        if player is None or not player.resume():
            raise HTTPException(status_code=404, detail="Nothing is paused.")
        return PlayerStateOut.from_player(player, guild)

    @app.post("/guilds/{guild_id}/music/skip", response_model=PlayerStateOut)
    async def skip(guild_id: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        if player is None or not await player.skip():
            raise HTTPException(status_code=404, detail="Nothing is playing.")
        await _get_service(request).record_event(guild_id, "skipped (dashboard)")
        return PlayerStateOut.from_player(player, guild)

    @app.post("/guilds/{guild_id}/music/restart", response_model=PlayerStateOut)
    async def restart(guild_id: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        if player is None or not await player.restart():
            raise HTTPException(status_code=404, detail="Nothing is playing.")
        return PlayerStateOut.from_player(player, guild)

    @app.post("/guilds/{guild_id}/music/stop", response_model=PlayerStateOut)
    async def stop(guild_id: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        if player is None or not player.is_playing():
            raise HTTPException(status_code=404, detail="Nothing is playing.")
        player.stop()
        MUSIC_QUEUE_LENGTH.labels(guild_id=str(guild_id)).set(0)
        await _get_service(request).record_event(guild_id, "stopped · queue cleared (dashboard)")
        return PlayerStateOut.from_player(player, guild)

    @app.post("/guilds/{guild_id}/music/volume", response_model=PlayerStateOut)
    async def set_volume(guild_id: int, body: VolumeRequest, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = await _get_service(request).get_or_create_player(guild_id)
        player.set_volume(body.percent)
        return PlayerStateOut.from_player(player, guild)

    @app.delete("/guilds/{guild_id}/music/queue/{index}", response_model=PlayerStateOut)
    async def remove_queue_item(guild_id: int, index: int, request: Request) -> PlayerStateOut:
        guild = _get_guild(request, guild_id)
        player = _get_service(request).get_player(guild_id)
        if player is None:
            raise HTTPException(status_code=404, detail="No active player.")
        try:
            player.remove_at(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="That queue item is no longer there.") from exc
        MUSIC_QUEUE_LENGTH.labels(guild_id=str(guild_id)).set(len(player.queue))
        return PlayerStateOut.from_player(player, guild)

    return app
