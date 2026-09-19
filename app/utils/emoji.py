"""Emoji-string normalization for reaction roles.

Kept out of the service layer (app/services/role_binding_service.py) so
that module can stay discord.py-free, matching the rest of app/services/:
services take/return plain Python, never discord.py types.
"""

from __future__ import annotations

import discord


class InvalidEmojiError(Exception):
    """Raised when text can't be parsed as an emoji. Message is user-safe."""


def normalize_emoji(raw: str) -> str:
    """Canonicalize user-provided emoji text to match incoming reaction events.

    Handles both custom emoji markup (`<:name:id>`, `<a:name:id>`) and
    unicode emoji, producing the same string form `str(payload.emoji)`
    yields on `on_raw_reaction_add`/`remove`, so a stored binding can be
    matched by plain string equality.

    This does NOT fully validate real unicode emoji - arbitrary text falls
    through as "assume unicode emoji" (PartialEmoji.from_str()'s own
    documented behavior) and is only truly rejected when Discord's API
    refuses the add_reaction() call the caller makes at creation time.
    """
    raw = raw.strip()
    if not raw:
        raise InvalidEmojiError("Emoji cannot be empty.")
    try:
        return str(discord.PartialEmoji.from_str(raw))
    except Exception as exc:
        raise InvalidEmojiError(f"{raw!r} doesn't look like a valid emoji.") from exc
