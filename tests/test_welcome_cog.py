"""Tests for the WelcomeCog's on_member_join listener.

Discord objects are duck-typed fakes per AGENTS.md's testing guidance
(see tests/test_permissions.py), except for the "found" channel case,
which needs to satisfy `isinstance(x, discord.TextChannel)` (the cog
checks this to guard against a stale/wrong channel id) - a MagicMock(spec=...)
handles that without needing a live discord.py gateway object.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from app.cogs.welcome import WelcomeCog


class FakeRole:
    def __init__(self, role_id: int, position: int) -> None:
        self.id = role_id
        self.position = position

    def __gt__(self, other: FakeRole) -> bool:
        return self.position > other.position


class FakeBotMember:
    """Stand-in for guild.me - only needs a top_role for bot_can_manage_role."""

    def __init__(self, top_role: FakeRole) -> None:
        self.top_role = top_role


class FakeGuild:
    def __init__(self, guild_id: int, *, member_count: int = 10) -> None:
        self.id = guild_id
        self.name = "Test Guild"
        self.member_count = member_count
        self.owner_id = 1
        self.me = FakeBotMember(top_role=FakeRole(role_id=0, position=100))
        self.channels: dict[int, object] = {}
        self.roles: dict[int, FakeRole] = {}

    def get_channel(self, channel_id: int) -> object | None:
        return self.channels.get(channel_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)


class FakeMember:
    def __init__(self, member_id: int, guild: FakeGuild, *, dm_forbidden: bool = False) -> None:
        self.id = member_id
        self.guild = guild
        self.mention = f"<@{member_id}>"
        self.added_roles: list[FakeRole] = []
        self.sent_dms: list[str | None] = []
        self._dm_forbidden = dm_forbidden

    def __str__(self) -> str:
        return "NewUser"

    async def add_roles(self, role: FakeRole, reason: str | None = None) -> None:
        self.added_roles.append(role)

    async def send(self, content: str | None = None, **_: object) -> None:
        if self._dm_forbidden:
            raise discord.Forbidden(MagicMock(status=403), "Cannot send messages to this user")
        self.sent_dms.append(content)


def _make_channel(channel_id: int) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = "welcome"
    channel.mention = f"<#{channel_id}>"
    channel.send = AsyncMock()
    return channel


def _make_cog() -> WelcomeCog:
    bot = SimpleNamespace(default_prefix="!")
    return WelcomeCog(bot)  # type: ignore[arg-type]


async def test_on_member_join_disabled_is_a_noop(db_session: AsyncSession) -> None:
    cog = _make_cog()
    guild = FakeGuild(111)
    member = FakeMember(1, guild)

    await cog.on_member_join(member)  # type: ignore[arg-type]

    assert member.sent_dms == []
    assert member.added_roles == []


async def test_on_member_join_missing_channel_still_runs_other_actions(
    db_session: AsyncSession,
) -> None:
    cog = _make_cog()
    guild = FakeGuild(222)
    role = FakeRole(role_id=42, position=1)
    guild.roles[42] = role
    member = FakeMember(2, guild)

    await cog.welcome_service.set_enabled(guild.id, True)
    await cog.welcome_service.set_channel(guild.id, 9999)  # never registered on the guild fake
    await cog.welcome_service.set_dm_enabled(guild.id, True)
    await cog.welcome_service.set_role(guild.id, 42)

    await cog.on_member_join(member)  # type: ignore[arg-type]

    # The missing channel doesn't raise, and the other enabled actions still run.
    assert member.sent_dms == ["Welcome to Test Guild!"]
    assert member.added_roles == [role]


async def test_on_member_join_sends_rendered_channel_message(db_session: AsyncSession) -> None:
    cog = _make_cog()
    guild = FakeGuild(333)
    channel = _make_channel(55)
    guild.channels[55] = channel
    member = FakeMember(3, guild)

    await cog.welcome_service.set_enabled(guild.id, True)
    await cog.welcome_service.set_channel(guild.id, 55)
    await cog.welcome_service.set_message(guild.id, "Hi {user_mention}, welcome to {server}!")

    await cog.on_member_join(member)  # type: ignore[arg-type]

    channel.send.assert_awaited_once_with("Hi <@3>, welcome to Test Guild!")


async def test_on_member_join_role_above_bot_is_skipped_not_raised(db_session: AsyncSession) -> None:
    cog = _make_cog()
    guild = FakeGuild(444)
    # Bot's top role is position 100 (see FakeGuild.me); this role outranks it.
    guild.roles[7] = FakeRole(role_id=7, position=200)
    member = FakeMember(4, guild)

    await cog.welcome_service.set_enabled(guild.id, True)
    await cog.welcome_service.set_role(guild.id, 7)

    await cog.on_member_join(member)  # type: ignore[arg-type]

    assert member.added_roles == []


async def test_on_member_join_dm_forbidden_does_not_raise(db_session: AsyncSession) -> None:
    cog = _make_cog()
    guild = FakeGuild(555)
    member = FakeMember(5, guild, dm_forbidden=True)

    await cog.welcome_service.set_enabled(guild.id, True)
    await cog.welcome_service.set_dm_enabled(guild.id, True)

    await cog.on_member_join(member)  # type: ignore[arg-type]

    assert member.sent_dms == []
