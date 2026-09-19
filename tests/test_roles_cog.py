"""Tests for RolesCog's raw reaction listener and component interactions.

Discord objects are duck-typed fakes per AGENTS.md's testing guidance (see
tests/test_permissions.py) - RawReactionActionEvent and Interaction aren't
practical to construct directly (their real __init__s expect raw gateway
payload dicts), so plain SimpleNamespace fakes stand in. The one exception
is `interaction.user`, which the cog checks with
`isinstance(interaction.user, discord.Member)` - a plain fake would fail
that check, so it uses `unittest.mock.MagicMock(spec=discord.Member)`
instead (spec'd mocks satisfy isinstance).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from app.cogs.roles import RolesCog

GUILD_ID = 555


class FakeRole:
    def __init__(self, role_id: int, position: int) -> None:
        self.id = role_id
        self.position = position
        self.mention = f"<@&{role_id}>"

    def __gt__(self, other: FakeRole) -> bool:
        return self.position > other.position


class FakeBotMember:
    def __init__(self, top_role: FakeRole) -> None:
        self.top_role = top_role


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id
        self.added_roles: list[FakeRole] = []
        self.removed_roles: list[FakeRole] = []

    async def add_roles(self, role: FakeRole, reason: str | None = None) -> None:
        self.added_roles.append(role)

    async def remove_roles(self, role: FakeRole, reason: str | None = None) -> None:
        self.removed_roles.append(role)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.me = FakeBotMember(top_role=FakeRole(0, 100))
        self.roles: dict[int, FakeRole] = {}
        self.members: dict[int, FakeMember] = {}

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, user_id: int) -> FakeMember | None:
        return self.members.get(user_id)


class FakeBot:
    def __init__(self, guild: FakeGuild) -> None:
        self.user = SimpleNamespace(id=999)  # the bot's own user id
        self.default_prefix = "!"
        self._guild = guild

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guild if self._guild.id == guild_id else None


def _payload(*, message_id=100, user_id=1, emoji="🎮", guild_id=GUILD_ID, member=None):
    return SimpleNamespace(
        guild_id=guild_id,
        channel_id=1,
        message_id=message_id,
        user_id=user_id,
        emoji=emoji,
        member=member,
    )


def _make_cog(guild: FakeGuild) -> RolesCog:
    return RolesCog(FakeBot(guild))  # type: ignore[arg-type]


async def test_reaction_add_grants_role(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    role = FakeRole(role_id=3, position=1)
    guild.roles[3] = role
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )
    member = FakeMember(1)

    await cog.on_raw_reaction_add(_payload(member=member))  # type: ignore[arg-type]

    assert member.added_roles == [role]


async def test_reaction_remove_revokes_role_when_toggle_true(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    role = FakeRole(role_id=3, position=1)
    guild.roles[3] = role
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3, toggle=True
    )
    member = FakeMember(1)
    guild.members[1] = member

    await cog.on_raw_reaction_remove(_payload())  # type: ignore[arg-type]

    assert member.removed_roles == [role]


async def test_reaction_remove_keeps_role_when_toggle_false(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    role = FakeRole(role_id=3, position=1)
    guild.roles[3] = role
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3, toggle=False
    )
    member = FakeMember(1)
    guild.members[1] = member

    await cog.on_raw_reaction_remove(_payload())  # type: ignore[arg-type]

    assert member.removed_roles == []


async def test_ignores_bots_own_reaction(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )
    member = FakeMember(999)

    # user_id matches FakeBot.user.id (999) - the bot reacting to seed the message.
    await cog.on_raw_reaction_add(_payload(user_id=999, member=member))  # type: ignore[arg-type]

    assert member.added_roles == []


async def test_ignores_dm_reactions(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog(guild)
    member = FakeMember(1)

    await cog.on_raw_reaction_add(_payload(guild_id=None, member=member))  # type: ignore[arg-type]

    assert member.added_roles == []


async def test_no_binding_is_a_noop(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog(guild)
    member = FakeMember(1)

    await cog.on_raw_reaction_add(_payload(emoji="🎵", member=member))  # type: ignore[arg-type]

    assert member.added_roles == []


async def test_disabled_binding_is_a_noop(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    guild.roles[3] = FakeRole(role_id=3, position=1)
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )
    await cog.role_service.set_message_enabled(GUILD_ID, 100, False)
    member = FakeMember(1)

    await cog.on_raw_reaction_add(_payload(member=member))  # type: ignore[arg-type]

    assert member.added_roles == []


async def test_missing_role_does_not_raise(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)  # role 3 never registered - simulates a deleted role
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )
    member = FakeMember(1)

    await cog.on_raw_reaction_add(_payload(member=member))  # type: ignore[arg-type]

    assert member.added_roles == []


async def test_role_above_bot_is_skipped_not_raised(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    guild.roles[3] = FakeRole(role_id=3, position=200)  # bot's top role is position 100
    cog = _make_cog(guild)
    await cog.role_service.create_reaction_binding(
        GUILD_ID, channel_id=1, message_id=100, emoji="🎮", role_id=3
    )
    member = FakeMember(1)

    await cog.on_raw_reaction_add(_payload(member=member))  # type: ignore[arg-type]

    assert member.added_roles == []


# --- Button/select component interactions ---


class FakeInteractionResponse:
    def __init__(self) -> None:
        self.messages: list[str | None] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str | None = None, *, ephemeral: bool = False, **_: object) -> None:
        self.messages.append(content)
        self._done = True


class FakeInteractionFollowup:
    def __init__(self) -> None:
        self.messages: list[str | None] = []

    async def send(self, content: str | None = None, *, ephemeral: bool = False, **_: object) -> None:
        self.messages.append(content)


def _make_member_mock(member_id: int, roles: list[FakeRole] | None = None) -> MagicMock:
    # isinstance(interaction.user, discord.Member) is checked by the cog, so
    # a plain duck-typed fake won't do here - spec'd mocks satisfy isinstance.
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.mention = f"<@{member_id}>"
    member.roles = list(roles or [])

    async def _add_roles(role: FakeRole, reason: str | None = None) -> None:
        member.roles.append(role)

    async def _remove_roles(role: FakeRole, reason: str | None = None) -> None:
        member.roles.remove(role)

    member.add_roles = AsyncMock(side_effect=_add_roles)
    member.remove_roles = AsyncMock(side_effect=_remove_roles)
    return member


def _make_component_interaction(
    *,
    guild: FakeGuild,
    message_id: int,
    user: MagicMock,
    custom_id: str | None = None,
    values: list[str] | None = None,
) -> SimpleNamespace:
    data: dict[str, object] = {}
    if custom_id is not None:
        data["custom_id"] = custom_id
    if values is not None:
        data["values"] = values
    return SimpleNamespace(
        type=discord.InteractionType.component,
        data=data,
        guild=guild,
        message=SimpleNamespace(id=message_id),
        user=user,
        response=FakeInteractionResponse(),
        followup=FakeInteractionFollowup(),
    )


async def test_button_interaction_adds_role_when_missing(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    role = FakeRole(role_id=3, position=1)
    guild.roles[3] = role
    cog = _make_cog(guild)
    await cog.role_service.create_button_binding(
        GUILD_ID, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:abc"
    )
    member = _make_member_mock(1)
    interaction = _make_component_interaction(
        guild=guild, message_id=100, user=member, custom_id="c3p0:rolebtn:abc"
    )

    await cog.on_interaction(interaction)  # type: ignore[arg-type]

    assert member.roles == [role]
    assert interaction.response.messages == ["➕ Added <@&3>."]


async def test_button_interaction_removes_role_when_present(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    role = FakeRole(role_id=3, position=1)
    guild.roles[3] = role
    cog = _make_cog(guild)
    await cog.role_service.create_button_binding(
        GUILD_ID, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:abc"
    )
    member = _make_member_mock(1, roles=[role])
    interaction = _make_component_interaction(
        guild=guild, message_id=100, user=member, custom_id="c3p0:rolebtn:abc"
    )

    await cog.on_interaction(interaction)  # type: ignore[arg-type]

    assert member.roles == []
    assert interaction.response.messages == ["➖ Removed <@&3>."]


async def test_button_interaction_unknown_custom_id_is_ignored_safely(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    cog = _make_cog(guild)
    member = _make_member_mock(1)
    interaction = _make_component_interaction(
        guild=guild, message_id=100, user=member, custom_id="c3p0:rolebtn:doesnotexist"
    )

    await cog.on_interaction(interaction)  # type: ignore[arg-type]

    assert member.roles == []
    assert interaction.response.messages == ["⚠️ This button isn't configured anymore."]


async def test_button_interaction_disabled_binding_is_a_noop(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    guild.roles[3] = FakeRole(role_id=3, position=1)
    cog = _make_cog(guild)
    await cog.role_service.create_button_binding(
        GUILD_ID, channel_id=1, message_id=100, role_id=3, custom_id="c3p0:rolebtn:abc"
    )
    await cog.role_service.set_message_enabled(GUILD_ID, 100, False)
    member = _make_member_mock(1)
    interaction = _make_component_interaction(
        guild=guild, message_id=100, user=member, custom_id="c3p0:rolebtn:abc"
    )

    await cog.on_interaction(interaction)  # type: ignore[arg-type]

    assert member.roles == []


async def test_select_interaction_grants_and_revokes_based_on_selection(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    role_a = FakeRole(role_id=3, position=1)
    role_b = FakeRole(role_id=4, position=1)
    guild.roles[3] = role_a
    guild.roles[4] = role_b
    cog = _make_cog(guild)
    binding_a = await cog.role_service.create_select_binding(
        GUILD_ID, channel_id=1, message_id=100, role_id=3
    )
    binding_b = await cog.role_service.create_select_binding(
        GUILD_ID, channel_id=1, message_id=100, role_id=4
    )
    # Member already has role_b (option b), selects only option a - should
    # gain role_a and lose role_b in the same interaction.
    member = _make_member_mock(1, roles=[role_b])
    interaction = _make_component_interaction(
        guild=guild, message_id=100, user=member, custom_id="c3p0:roleselect:100", values=[str(binding_a.id)]
    )

    await cog.on_interaction(interaction)  # type: ignore[arg-type]

    assert set(member.roles) == {role_a}
    assert interaction.response.messages == ["Added <@&3> / Removed <@&4>"]
    assert binding_b.role_id == 4  # sanity - id used as option value, not role_id


async def test_select_interaction_skips_roles_bot_cannot_manage(db_session: AsyncSession) -> None:
    guild = FakeGuild(GUILD_ID)
    guild.roles[3] = FakeRole(role_id=3, position=200)  # above bot's top role (position 100)
    cog = _make_cog(guild)
    binding = await cog.role_service.create_select_binding(
        GUILD_ID, channel_id=1, message_id=100, role_id=3
    )
    member = _make_member_mock(1)
    interaction = _make_component_interaction(
        guild=guild, message_id=100, user=member, custom_id="c3p0:roleselect:100", values=[str(binding.id)]
    )

    await cog.on_interaction(interaction)  # type: ignore[arg-type]

    assert member.roles == []
