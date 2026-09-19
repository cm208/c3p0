from __future__ import annotations

import discord

from app.web.permission_groups import CHANNEL_OVERWRITE_PERMISSIONS, PERMISSION_GROUPS

# Deprecated aliases in discord.py's VALID_FLAGS that share a bit with a
# canonical name already covered by PERMISSION_GROUPS - excluded here the
# same way the module's own docstring explains.
_DEPRECATED_ALIASES = frozenset(
    {"read_messages", "external_emojis", "manage_permissions", "manage_emojis", "external_stickers", "send_polls"}
)


def _canonical_flags() -> set[str]:
    return set(discord.Permissions.VALID_FLAGS) - _DEPRECATED_ALIASES


def test_permission_groups_cover_every_canonical_flag_with_no_duplicates() -> None:
    all_grouped: list[str] = [name for names in PERMISSION_GROUPS.values() for name in names]

    assert len(all_grouped) == len(set(all_grouped)), "a flag appears in more than one group"
    assert set(all_grouped) == _canonical_flags()


def test_channel_overwrite_permissions_are_all_valid_flags() -> None:
    canonical = _canonical_flags()
    for name in CHANNEL_OVERWRITE_PERMISSIONS:
        assert name in canonical, f"{name!r} is not a valid discord.py permission flag"
