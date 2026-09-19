"""Tests for ModerationCog's prefix commands.

Cog prefix commands are invoked directly via `cog.kick.callback(cog, ctx, ...)`
rather than through discord.py's full Command/permission-check pipeline.
`@commands.command()` turns the method into a `Command` object whose
`callback` is the *original, unbound* function - discord.py's own dispatcher
calls `self.callback(self.cog, context, ...)` (see
discord/ext/commands/core.py), so `cog.kick` here is that Command object,
not a bound method. This bypasses `@commands.has_permissions` (evaluated
separately by discord.py's dispatcher, never exercised in this file) and
tests only the business logic, consistent with the rest of this suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from app.cogs.moderation import ModerationCog
from app.db.models.infraction import InfractionType

GUILD_ID = 777
# Deliberately far from any test member id, so "target.id == guild.owner_id"
# (moderator_can_moderate's "can't act on the owner" branch) never
# accidentally triggers for an unrelated test.
OWNER_ID = 999999


class FakeRole:
    def __init__(self, position: int) -> None:
        self.position = position

    def __gt__(self, other: FakeRole) -> bool:
        return self.position > other.position


class FakeTargetMember:
    def __init__(self, member_id: int, top_role_position: int = 1) -> None:
        self.id = member_id
        self.top_role = FakeRole(top_role_position)
        self.mention = f"<@{member_id}>"
        self.kick = AsyncMock()
        self.ban = AsyncMock()
        self.timeout = AsyncMock()

    def __str__(self) -> str:
        return f"Target#{self.id}"


class FakeGuild:
    def __init__(self, guild_id: int, *, owner_id: int = OWNER_ID, bot_top_role_position: int = 100) -> None:
        self.id = guild_id
        self.owner_id = owner_id
        self.default_role = SimpleNamespace(name="@everyone")
        self.me = SimpleNamespace(top_role=FakeRole(bot_top_role_position))
        self.channels: dict[int, object] = {}
        self.unban = AsyncMock()

    def get_channel(self, channel_id: int) -> object | None:
        return self.channels.get(channel_id)


def _make_moderator(guild: FakeGuild, *, moderator_id: int = 500, top_role_position: int = 50) -> MagicMock:
    # isinstance(ctx.author, discord.Member) is checked by the cog, so a
    # plain duck-typed fake won't do here - a spec'd mock satisfies isinstance.
    author = MagicMock(spec=discord.Member)
    author.id = moderator_id
    author.guild = guild
    author.top_role = FakeRole(top_role_position)
    author.mention = f"<@{moderator_id}>"
    return author


class FakeContext:
    def __init__(self, guild: FakeGuild, author: MagicMock, channel: object | None = None) -> None:
        self.guild = guild
        self.author = author
        self.channel = channel
        self.sent: list[object] = []

    async def send(self, content: object | None = None, *, embed: object | None = None, **_: object) -> None:
        self.sent.append(content if content is not None else embed)


def _make_cog() -> ModerationCog:
    bot = SimpleNamespace(default_prefix="!")
    return ModerationCog(bot)  # type: ignore[arg-type]


async def test_kick_success_records_infraction(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    target = FakeTargetMember(1)
    ctx = FakeContext(guild, author)

    await cog.kick.callback(cog, ctx, target, reason="spam")  # type: ignore[arg-type]

    target.kick.assert_awaited_once()
    infractions = await cog.service.list_infractions(GUILD_ID, target.id)
    assert len(infractions) == 1
    assert infractions[0].type == InfractionType.KICK
    assert any("Kicked" in str(m) for m in ctx.sent)


async def test_kick_blocked_when_target_outranks_moderator(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild, top_role_position=10)
    target = FakeTargetMember(1, top_role_position=50)  # outranks the moderator
    ctx = FakeContext(guild, author)

    await cog.kick.callback(cog, ctx, target, reason="spam")  # type: ignore[arg-type]

    target.kick.assert_not_awaited()
    assert await cog.service.list_infractions(GUILD_ID, target.id) == []


async def test_kick_blocked_when_bot_cannot_moderate(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID, bot_top_role_position=5)
    cog = _make_cog()
    # Moderator clearly outranks the target, so this test isolates the bot's
    # own hierarchy check rather than accidentally tripping the moderator one.
    author = _make_moderator(guild, top_role_position=200)
    target = FakeTargetMember(1, top_role_position=50)  # above the bot's own top role (5)
    ctx = FakeContext(guild, author)

    await cog.kick.callback(cog, ctx, target, reason="spam")  # type: ignore[arg-type]

    target.kick.assert_not_awaited()


async def test_timeout_parses_duration_and_applies(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    target = FakeTargetMember(1)
    ctx = FakeContext(guild, author)

    await cog.timeout.callback(cog, ctx, target, "10m", reason="cooldown")  # type: ignore[arg-type]

    target.timeout.assert_awaited_once()
    infractions = await cog.service.list_infractions(GUILD_ID, target.id)
    assert infractions[0].duration_seconds == 600


async def test_timeout_rejects_invalid_duration(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    target = FakeTargetMember(1)
    ctx = FakeContext(guild, author)

    await cog.timeout.callback(cog, ctx, target, "not-a-duration")  # type: ignore[arg-type]

    target.timeout.assert_not_awaited()
    assert any("valid duration" in str(m) for m in ctx.sent)


async def test_warn_triggers_kick_escalation(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    target = FakeTargetMember(1)
    ctx = FakeContext(guild, author)
    await cog.service.set_escalation_enabled(GUILD_ID, True)
    await cog.service.set_escalation_threshold(GUILD_ID, 2, InfractionType.KICK)

    await cog.warn.callback(cog, ctx, target, reason="rule 1")  # type: ignore[arg-type]
    await cog.warn.callback(cog, ctx, target, reason="rule 2")  # type: ignore[arg-type]

    target.kick.assert_awaited_once()
    infractions = await cog.service.list_infractions(GUILD_ID, target.id)
    assert sorted(i.type.value for i in infractions) == sorted(["warn", "warn", "kick"])


async def test_warn_does_not_escalate_when_disabled(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    target = FakeTargetMember(1)
    ctx = FakeContext(guild, author)
    await cog.service.set_escalation_threshold(GUILD_ID, 1, InfractionType.KICK)  # left disabled

    await cog.warn.callback(cog, ctx, target, reason="rule 1")  # type: ignore[arg-type]

    target.kick.assert_not_awaited()


async def test_unban_resolves_active_ban_infractions(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    ctx = FakeContext(guild, author)
    user = FakeTargetMember(1)
    await cog.service.record_infraction(
        GUILD_ID, user_id=user.id, moderator_id=author.id, type=InfractionType.BAN
    )

    await cog.unban.callback(cog, ctx, user, reason="appeal accepted")  # type: ignore[arg-type]

    guild.unban.assert_awaited_once()
    active = await cog.service.list_infractions(GUILD_ID, user.id, active_only=True)
    assert active == []


async def test_clear_rejects_out_of_range_amount(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    channel = MagicMock(spec=discord.TextChannel)
    channel.purge = AsyncMock()
    ctx = FakeContext(guild, author, channel=channel)

    await cog.clear.callback(cog, ctx, 0)  # type: ignore[arg-type]

    channel.purge.assert_not_awaited()
    assert any("at least 1" in str(m) for m in ctx.sent)


async def test_clear_purges_messages(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    author = _make_moderator(guild)
    channel = MagicMock(spec=discord.TextChannel)
    channel.purge = AsyncMock(return_value=[object(), object()])
    ctx = FakeContext(guild, author, channel=channel)

    await cog.clear.callback(cog, ctx, 5)  # type: ignore[arg-type]

    channel.purge.assert_awaited_once_with(limit=5)
