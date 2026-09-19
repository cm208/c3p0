"""Discord message-link parsing.

Slash commands have no built-in "message" option type (Discord only offers
string/integer/boolean/user/channel/role/mentionable/number/attachment), so
admin commands that operate on an existing message (reaction/button/select
role bindings) accept a message link - right-click a message, "Copy Message
Link" - instead of separate channel+ID parameters.
"""

from __future__ import annotations

import re

_MESSAGE_LINK_RE = re.compile(
    # (?<![\w.]) blocks a `search()`-matched substring from being a suffix of a
    # longer hostname label, e.g. "notdiscord.com/channels/..." or
    # "evil.discord.com/channels/..." - only a start-of-string/whitespace/URL
    # scheme boundary (or "canary."/"ptb.") may precede "discord".
    r"(?<![\w.])(?:https?://)?(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)"
)


class InvalidMessageLinkError(Exception):
    """Raised when a string isn't a recognizable Discord message link. Message is user-safe."""


def parse_message_link(link: str) -> tuple[int, int, int]:
    """Return (guild_id, channel_id, message_id) parsed from a message link."""
    match = _MESSAGE_LINK_RE.search(link.strip())
    if not match:
        raise InvalidMessageLinkError(
            "That doesn't look like a message link. Right-click a message and choose "
            "'Copy Message Link'."
        )
    guild_id, channel_id, message_id = (int(group) for group in match.groups())
    return guild_id, channel_id, message_id
