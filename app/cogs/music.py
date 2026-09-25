"""Music cog.

Prefix commands rather than slash commands. Playback state lives in
a per-guild GuildPlayer (app/music/guild_player.py) held by MusicService -
this cog's job is command handling, permission/DJ-role checks, and
translating GuildPlayer/AudioProvider results into user-facing messages.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from app.metrics import MUSIC_FAILURES, MUSIC_PLAYS, MUSIC_QUEUE_LENGTH
from app.music.audio_provider import AudioResolutionError
from app.music.guild_player import LoopMode, QueueFullError
from app.services.music_service import MusicService

logger = logging.getLogger(__name__)

_QUEUE_DISPLAY_LIMIT = 10


def _requester_display_name(guild: discord.Guild, user_id: int) -> str:
    """Mirrors app/music/internal_api.py's _track_out name resolution -
    same "guild's own live member cache, fall back to a bare id" logic,
    just for the Discord-side !queue embed instead of the web dashboard."""
    member = guild.get_member(user_id)
    return member.display_name if member is not None else f"user {user_id}"


class MusicCog(commands.Cog, name="Music"):
    """Play music in a voice channel (prefix commands)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = MusicService(default_prefix=bot.default_prefix)

    async def _check_dj(self, ctx: commands.Context) -> bool:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        config = await self.service.get_config(ctx.guild.id)
        if config.dj_role_id is None:
            return True
        if ctx.author.guild_permissions.manage_guild:
            return True
        if any(role.id == config.dj_role_id for role in ctx.author.roles):
            return True
        await ctx.send("⚠️ You need the DJ role to do that.")
        return False

    def _update_queue_metric(self, guild_id: int) -> None:
        player = self.service.get_player(guild_id)
        MUSIC_QUEUE_LENGTH.labels(guild_id=str(guild_id)).set(len(player.queue) if player else 0)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        player = self.service.get_player(member.guild.id)
        if player is None or player.voice_client is None or not player.is_connected():
            return
        channel = player.voice_client.channel
        if len(channel.members) == 1 and channel.members[0].id == self.bot.user.id:
            await player.disconnect()
            self.service.remove_player(member.guild.id)
            logger.info("Left an empty voice channel", extra={"guild_id": member.guild.id})

    @commands.command(name="join")
    @commands.guild_only()
    async def join(self, ctx: commands.Context) -> None:
        """Join your current voice channel."""
        assert isinstance(ctx.author, discord.Member) and ctx.guild is not None
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("⚠️ You need to be in a voice channel.")
            return

        player = await self.service.get_or_create_player(ctx.guild.id)
        try:
            await player.connect(ctx.author.voice.channel)
        except (discord.ClientException, discord.Forbidden) as exc:
            await ctx.send(f"⚠️ Couldn't join that channel ({exc}).")
            return
        await ctx.send(f"✅ Joined {ctx.author.voice.channel.mention}.")

    @commands.command(name="leave")
    @commands.guild_only()
    async def leave(self, ctx: commands.Context) -> None:
        """Leave the voice channel and clear the queue."""
        assert ctx.guild is not None
        player = self.service.get_player(ctx.guild.id)
        if player is None or not player.is_connected():
            await ctx.send("⚠️ I'm not connected to a voice channel.")
            return
        await player.disconnect()
        self.service.remove_player(ctx.guild.id)
        await ctx.send("👋 Left the voice channel.")

    @commands.command(name="play")
    @commands.guild_only()
    async def play(
        self,
        ctx: commands.Context,
        *,
        query: str = commands.parameter(
            description="A song name to search for, or a direct YouTube URL."
        ),
    ) -> None:
        """Play a song, or add it to the queue if something's already playing."""
        assert isinstance(ctx.author, discord.Member) and ctx.guild is not None
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("⚠️ You need to be in a voice channel.")
            return

        config = await self.service.get_config(ctx.guild.id)
        if not config.enabled:
            await ctx.send("⚠️ Music is disabled on this server.")
            return

        player = await self.service.get_or_create_player(ctx.guild.id)
        if not player.is_connected():
            try:
                await player.connect(ctx.author.voice.channel)
            except (discord.ClientException, discord.Forbidden) as exc:
                await ctx.send(f"⚠️ Couldn't join your voice channel ({exc}).")
                return

        async with ctx.typing():
            try:
                track, position = await player.resolve_and_enqueue(query, requested_by=ctx.author.id)
            except AudioResolutionError as exc:
                MUSIC_FAILURES.labels(reason="resolve_error").inc()
                await ctx.send(f"⚠️ {exc}")
                return
            except QueueFullError as exc:
                MUSIC_FAILURES.labels(reason="queue_full").inc()
                await ctx.send(f"⚠️ {exc}")
                return

        self._update_queue_metric(ctx.guild.id)

        if not player.is_playing():
            try:
                started = await player.start_or_advance()
            except discord.ClientException as exc:
                MUSIC_FAILURES.labels(reason="playback_error").inc()
                await ctx.send(f"⚠️ Couldn't start playback ({exc}).")
                return
            if started:
                MUSIC_PLAYS.inc()
                await ctx.send(f"▶️ Now playing **{track.title}** — requested by {ctx.author.mention}.")
            else:
                await ctx.send("⚠️ Nothing to play.")
        else:
            await ctx.send(f"➕ Queued **{track.title}** (position {position}) — requested by {ctx.author.mention}.")
        await self.service.record_event(ctx.guild.id, f'queued "{track.title}" by @{ctx.author}')

    @commands.command(name="pause")
    @commands.guild_only()
    async def pause(self, ctx: commands.Context) -> None:
        """Pause the current track."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        player = self.service.get_player(ctx.guild.id)
        if player is None or not player.pause():
            await ctx.send("⚠️ Nothing is playing.")
            return
        await ctx.send("⏸️ Paused.")

    @commands.command(name="resume")
    @commands.guild_only()
    async def resume(self, ctx: commands.Context) -> None:
        """Resume the paused track."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        player = self.service.get_player(ctx.guild.id)
        if player is None or not player.resume():
            await ctx.send("⚠️ Nothing is paused.")
            return
        await ctx.send("▶️ Resumed.")

    @commands.command(name="skip")
    @commands.guild_only()
    async def skip(self, ctx: commands.Context) -> None:
        """Skip the current track."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        player = self.service.get_player(ctx.guild.id)
        if player is None or not await player.skip():
            await ctx.send("⚠️ Nothing is playing.")
            return
        await ctx.send("⏭️ Skipped.")

    @commands.command(name="stop")
    @commands.guild_only()
    async def stop(self, ctx: commands.Context) -> None:
        """Stop playback and clear the queue (without leaving the voice channel)."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        player = self.service.get_player(ctx.guild.id)
        if player is None:
            await ctx.send("⚠️ Nothing is playing.")
            return
        player.stop()
        self._update_queue_metric(ctx.guild.id)
        await ctx.send("⏹️ Stopped and cleared the queue.")
        await self.service.record_event(ctx.guild.id, f"stopped · queue cleared by @{ctx.author}")

    @commands.command(name="queue")
    @commands.guild_only()
    async def queue(self, ctx: commands.Context) -> None:
        """Show what's playing and what's up next."""
        assert ctx.guild is not None
        player = self.service.get_player(ctx.guild.id)
        if player is None or (player.current is None and not player.queue):
            await ctx.send("The queue is empty.")
            return

        lines = []
        if player.current is not None:
            name = _requester_display_name(ctx.guild, player.current.requested_by)
            lines.append(f"**Now playing:** {player.current.title} — requested by {name}")
        upcoming = player.queue
        for i, track in enumerate(upcoming[:_QUEUE_DISPLAY_LIMIT], start=1):
            name = _requester_display_name(ctx.guild, track.requested_by)
            lines.append(f"{i}. {track.title} — requested by {name}")
        if len(upcoming) > _QUEUE_DISPLAY_LIMIT:
            lines.append(f"...and {len(upcoming) - _QUEUE_DISPLAY_LIMIT} more")

        embed = discord.Embed(title="Queue", description="\n".join(lines), color=discord.Color.blurple())
        await ctx.send(embed=embed)

    @commands.command(name="volume")
    @commands.guild_only()
    async def volume(
        self,
        ctx: commands.Context,
        level: int = commands.parameter(description="Volume percentage, from 0 to 100."),
    ) -> None:
        """Set the playback volume."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        if not 0 <= level <= 100:
            await ctx.send("⚠️ Volume must be between 0 and 100.")
            return
        player = await self.service.get_or_create_player(ctx.guild.id)
        player.set_volume(level)
        await ctx.send(f"🔊 Volume set to {level}%.")

    @commands.command(name="shuffle")
    @commands.guild_only()
    async def shuffle(self, ctx: commands.Context) -> None:
        """Shuffle the upcoming queue (does not affect the current track)."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        player = self.service.get_player(ctx.guild.id)
        if player is None or not player.queue:
            await ctx.send("⚠️ Nothing to shuffle.")
            return
        player.shuffle()
        await ctx.send("🔀 Queue shuffled.")

    @commands.command(name="loop")
    @commands.guild_only()
    async def loop(
        self,
        ctx: commands.Context,
        mode: str = commands.parameter(
            default="off", description="`off`, `song`, or `queue`. Defaults to `off`."
        ),
    ) -> None:
        """Set the loop mode: repeat nothing, the current song, or the whole queue."""
        assert ctx.guild is not None
        if not await self._check_dj(ctx):
            return
        try:
            loop_mode = LoopMode(mode.lower())
        except ValueError:
            await ctx.send("⚠️ Loop mode must be `off`, `song`, or `queue`.")
            return
        player = await self.service.get_or_create_player(ctx.guild.id)
        player.set_loop(loop_mode)
        await ctx.send(f"🔁 Loop mode set to `{loop_mode.value}`.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
