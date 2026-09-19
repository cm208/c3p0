"""Tests for app.utils.permissions.

Discord objects are duck-typed fakes rather than real discord.py instances,
per AGENTS.md's guidance to mock Discord interactions in unit tests. Only
the attributes/comparisons the permissions module actually touches are
implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.utils.permissions import (
    PermissionCheckError,
    bot_can_manage_role,
    bot_can_moderate,
    has_all_permission_bits,
    moderator_can_moderate,
    require_bot_can_manage_role,
    require_bot_can_moderate,
    require_moderator_can_moderate,
)


@dataclass(order=True)
class FakeRole:
    position: int
    name: str = field(default="role", compare=False)


@dataclass
class FakeMember:
    id: int
    top_role: FakeRole
    guild: FakeGuild = field(repr=False)


@dataclass
class FakeGuild:
    owner_id: int
    me: FakeMember | None = None


def _make_guild_with_bot(bot_position: int, owner_id: int = 1) -> FakeGuild:
    guild = FakeGuild(owner_id=owner_id)
    guild.me = FakeMember(id=999, top_role=FakeRole(bot_position), guild=guild)
    return guild


def test_bot_can_manage_role_true_when_above() -> None:
    guild = _make_guild_with_bot(bot_position=10)
    role = FakeRole(position=5)

    assert bot_can_manage_role(guild, role) is True


def test_bot_can_manage_role_false_when_equal_or_below() -> None:
    guild = _make_guild_with_bot(bot_position=5)
    role = FakeRole(position=5)

    assert bot_can_manage_role(guild, role) is False


def test_bot_can_manage_role_false_when_bot_not_in_guild() -> None:
    guild = FakeGuild(owner_id=1, me=None)
    role = FakeRole(position=1)

    assert bot_can_manage_role(guild, role) is False


def test_bot_can_moderate_respects_hierarchy() -> None:
    guild = _make_guild_with_bot(bot_position=10)
    target = FakeMember(id=2, top_role=FakeRole(5), guild=guild)

    assert bot_can_moderate(guild, target) is True


def test_bot_can_moderate_never_targets_owner() -> None:
    guild = _make_guild_with_bot(bot_position=999, owner_id=2)
    target = FakeMember(id=2, top_role=FakeRole(0), guild=guild)

    assert bot_can_moderate(guild, target) is False


def test_moderator_can_moderate_owner_always_can() -> None:
    guild = _make_guild_with_bot(bot_position=10, owner_id=1)
    owner = FakeMember(id=1, top_role=FakeRole(0), guild=guild)
    target = FakeMember(id=2, top_role=FakeRole(50), guild=guild)

    assert moderator_can_moderate(owner, target) is True


def test_moderator_cannot_moderate_owner() -> None:
    guild = _make_guild_with_bot(bot_position=10, owner_id=1)
    owner = FakeMember(id=1, top_role=FakeRole(50), guild=guild)
    moderator = FakeMember(id=2, top_role=FakeRole(20), guild=guild)

    assert moderator_can_moderate(moderator, owner) is False


def test_moderator_can_moderate_lower_role() -> None:
    guild = _make_guild_with_bot(bot_position=10, owner_id=1)
    moderator = FakeMember(id=2, top_role=FakeRole(20), guild=guild)
    target = FakeMember(id=3, top_role=FakeRole(10), guild=guild)

    assert moderator_can_moderate(moderator, target) is True


def test_moderator_cannot_moderate_equal_or_higher_role() -> None:
    guild = _make_guild_with_bot(bot_position=10, owner_id=1)
    moderator = FakeMember(id=2, top_role=FakeRole(10), guild=guild)
    target = FakeMember(id=3, top_role=FakeRole(10), guild=guild)

    assert moderator_can_moderate(moderator, target) is False


def test_require_helpers_raise_user_safe_errors() -> None:
    guild = _make_guild_with_bot(bot_position=1, owner_id=1)
    target = FakeMember(id=2, top_role=FakeRole(50), guild=guild)

    with pytest.raises(PermissionCheckError):
        require_bot_can_moderate(guild, target)

    moderator = FakeMember(id=3, top_role=FakeRole(10), guild=guild)
    with pytest.raises(PermissionCheckError):
        require_moderator_can_moderate(moderator, target)

    with pytest.raises(PermissionCheckError):
        require_bot_can_manage_role(guild, FakeRole(50))


# --- has_all_permission_bits ---

_KICK_MEMBERS = 0x2
_BAN_MEMBERS = 0x4
_ADMINISTRATOR = 0x8
_MANAGE_MESSAGES = 0x2000


def test_has_all_permission_bits_true_for_exact_subset() -> None:
    held = _KICK_MEMBERS | _BAN_MEMBERS | _MANAGE_MESSAGES
    assert has_all_permission_bits(held=held, requested=_KICK_MEMBERS | _BAN_MEMBERS) is True


def test_has_all_permission_bits_false_when_missing_a_bit() -> None:
    held = _KICK_MEMBERS
    assert has_all_permission_bits(held=held, requested=_KICK_MEMBERS | _BAN_MEMBERS) is False


def test_has_all_permission_bits_true_for_zero_requested() -> None:
    assert has_all_permission_bits(held=0, requested=0) is True


def test_has_all_permission_bits_administrator_implies_everything() -> None:
    assert has_all_permission_bits(held=_ADMINISTRATOR, requested=_KICK_MEMBERS | _BAN_MEMBERS) is True


def test_has_all_permission_bits_requesting_administrator_requires_holding_it() -> None:
    assert has_all_permission_bits(held=_KICK_MEMBERS, requested=_ADMINISTRATOR) is False
    assert has_all_permission_bits(held=_ADMINISTRATOR, requested=_ADMINISTRATOR) is True
