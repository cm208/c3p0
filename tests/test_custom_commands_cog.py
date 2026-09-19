"""Tests for CustomCommandsCog's on_message trigger dispatch.

Discord objects are duck-typed fakes per AGENTS.md's testing guidance,
except `message.author`, which the cog checks with
`isinstance(message.author, discord.Member)` - a plain fake would fail
that check, so it uses `unittest.mock.MagicMock(spec=discord.Member)`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from app.cogs.custom_commands import CustomCommandsCog

GUILD_ID = 555


class FakeGuild:
    def __init__(self, guild_id: int, *, member_count: int = 10) -> None:
        self.id = guild_id
        self.name = "Test Guild"
        self.member_count = member_count


class FakePermissions:
    def __init__(self, **granted: bool) -> None:
        self._granted = granted

    def __iter__(self):
        yield from self._granted.items()


def _make_member(
    member_id: int, guild: FakeGuild, *, role_ids: set[int] | None = None, permissions: FakePermissions | None = None
) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild = guild
    member.bot = False
    member.mention = f"<@{member_id}>"
    member.roles = [SimpleNamespace(id=rid) for rid in (role_ids or set())]
    member.guild_permissions = permissions or FakePermissions()
    member.__str__.return_value = "Author"  # documented MagicMock pattern for configuring str()
    return member


class FakeChannel:
    def __init__(self, name: str = "general") -> None:
        self.name = name
        self.mention = "<#1>"
        self.sent: list[object] = []

    async def send(self, content: object | None = None, *, embed: object | None = None, **_: object) -> None:
        self.sent.append(content if content is not None else embed)


def _make_message(author: MagicMock, guild: FakeGuild, channel: FakeChannel, content: str) -> SimpleNamespace:
    return SimpleNamespace(author=author, guild=guild, channel=channel, content=content)


def _make_cog() -> CustomCommandsCog:
    bot = SimpleNamespace(default_prefix="!", all_commands={})
    return CustomCommandsCog(bot)  # type: ignore[arg-type]


async def test_on_message_sends_rendered_response(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi {user_mention}!", created_by=1)
    channel = FakeChannel()
    author = _make_member(1, guild)
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == ["Hi <@1>!"]


async def test_on_message_ignores_bots(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    channel = FakeChannel()
    author = _make_member(1, guild)
    author.bot = True
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == []


async def test_on_message_ignores_disabled_command(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    await cog.service.set_enabled_by_trigger(GUILD_ID, "!rules", False)
    channel = FakeChannel()
    author = _make_member(1, guild)
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == []


async def test_on_message_matches_first_token_only(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    channel = FakeChannel()
    author = _make_member(1, guild)
    message = _make_message(author, guild, channel, "!rules please")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == ["Hi!"]


async def test_on_message_blocks_role_restricted_command_for_non_member(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    await cog.service.set_restriction_by_trigger(GUILD_ID, "!rules", restriction_type="role", restricted_role_id=99)
    channel = FakeChannel()
    author = _make_member(1, guild, role_ids={1, 2})  # doesn't have role 99
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == []


async def test_on_message_allows_role_restricted_command_for_member(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    await cog.service.set_restriction_by_trigger(GUILD_ID, "!rules", restriction_type="role", restricted_role_id=99)
    channel = FakeChannel()
    author = _make_member(1, guild, role_ids={99})
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == ["Hi!"]


async def test_on_message_blocks_permission_restricted_command(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    await cog.service.set_restriction_by_trigger(
        GUILD_ID, "!rules", restriction_type="permission", restricted_permission="manage_messages"
    )
    channel = FakeChannel()
    author = _make_member(1, guild, permissions=FakePermissions(manage_messages=False))
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert channel.sent == []


async def test_on_message_sends_embed_when_enabled(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    await cog.service.set_embed_enabled_by_trigger(GUILD_ID, "!rules", True)
    channel = FakeChannel()
    author = _make_member(1, guild)
    message = _make_message(author, guild, channel, "!rules")

    await cog.on_message(message)  # type: ignore[arg-type]

    assert len(channel.sent) == 1
    assert isinstance(channel.sent[0], discord.Embed)
    assert channel.sent[0].description == "Hi!"


async def test_on_message_enforces_cooldown(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog()
    await cog.service.create(GUILD_ID, name="Rules", trigger="!rules", response="Hi!", created_by=1)
    await cog.service.set_cooldown_by_trigger(GUILD_ID, "!rules", cooldown_type="user", cooldown_seconds=60)
    channel = FakeChannel()
    author = _make_member(1, guild)

    await cog.on_message(_make_message(author, guild, channel, "!rules"))  # type: ignore[arg-type]
    await cog.on_message(_make_message(author, guild, channel, "!rules"))  # type: ignore[arg-type]

    assert channel.sent[0] == "Hi!"
    assert "cooldown" in str(channel.sent[1])


async def test_check_trigger_collision_detects_builtin(db_session: AsyncSession) -> None:
    cog = _make_cog()
    cog.bot.all_commands = {"kick": object()}  # type: ignore[attr-defined]

    collision = await cog._check_trigger_collision(GUILD_ID, "!kick")

    assert collision == "kick"


async def test_check_trigger_collision_ignores_non_prefixed_trigger(db_session: AsyncSession) -> None:
    cog = _make_cog()
    cog.bot.all_commands = {"kick": object()}  # type: ignore[attr-defined]

    collision = await cog._check_trigger_collision(GUILD_ID, "kick")  # no prefix

    assert collision is None
