"""Discord message-component payload construction for the Roles page.

The discord.py-touching counterpart to app/utils/emoji.py, scoped to this
one page - the web process has no discord.py Client/View/Message objects,
only bot-token REST calls, so every component payload byte here is built
by hand rather than via discord.py's normal `discord.ui.View`. Every shape
below was verified against discord.py 2.7.1's own serialization code
(discord/components.py's Button.to_dict()/SelectMenu.to_dict()/
SelectOption.to_dict()), not guessed - this has to interoperate with
app/cogs/roles.py's existing, live listeners, which were built against
whatever discord.py itself sends.

normalize_emoji/InvalidEmojiError are reused as-is from app/utils/emoji.py
(confirmed safe: discord.PartialEmoji.from_str() is a pure classmethod,
no client/gateway needed).
"""

from __future__ import annotations

from dataclasses import dataclass

import discord

from app.utils.emoji import InvalidEmojiError, normalize_emoji  # noqa: F401 - re-exported

_BUTTON_STYLE_PRIMARY = 1
_COMPONENT_TYPE_ACTION_ROW = 1
_COMPONENT_TYPE_BUTTON = 2
_COMPONENT_TYPE_SELECT = 3
_MAX_PER_ACTION_ROW = 5  # Discord's cap on buttons per row (also selects: one per row)


def reaction_path_emoji(emoji: str) -> str:
    """The exact string Discord's reaction endpoints expect in the URL path.

    Mirrors discord/message.py's convert_emoji_reaction() for a plain str
    input verbatim (`emoji.strip('<>')`) - NOT PartialEmoji._as_reaction()'s
    cleaner `name:id` form. The two differ for every custom emoji (stripping
    '<>' off '<:name:id>' leaves a leading ':', off '<a:name:id>' leaves a
    leading 'a:') but .strip('<>') is what the already-shipped, working
    Discord-side feature actually sends - matching it, not the
    cleaner-looking alternative, is what keeps this wire-compatible.
    """
    return emoji.strip("<>")


def button_emoji_dict(emoji: str | None) -> dict | None:
    """A button component's "emoji" field, or None to omit it entirely."""
    if emoji is None:
        return None
    parsed = discord.PartialEmoji.from_str(emoji)
    payload: dict = {"id": parsed.id, "name": parsed.name}
    if parsed.animated:
        payload["animated"] = True
    return payload


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """What build_button_components needs per button - the caller resolves
    a RoleBindingView's role_id to a live role name (roles can be renamed
    or deleted; the binding itself only ever stores the id) before calling."""

    label: str
    custom_id: str
    emoji: str | None


@dataclass(frozen=True, slots=True)
class SelectOptionSpec:
    label: str
    value: str


def build_button_components(buttons_spec: list[ButtonSpec]) -> list[dict]:
    """Action-row components for a set of role buttons.

    Chunks into groups of 5 (Discord's per-row cap), one action row per
    group - matches RolesCog's own _MAX_COMPONENTS_PER_MESSAGE = 25 cap
    (5 rows x 5 buttons) and create_button_binding's len(buttons) >= 25
    rejection.
    """
    rows: list[dict] = []
    for start in range(0, len(buttons_spec), _MAX_PER_ACTION_ROW):
        chunk = buttons_spec[start : start + _MAX_PER_ACTION_ROW]
        buttons = []
        for spec in chunk:
            button: dict = {
                "type": _COMPONENT_TYPE_BUTTON,
                "style": _BUTTON_STYLE_PRIMARY,
                "disabled": False,
                "label": spec.label,
                "custom_id": spec.custom_id,
            }
            emoji = button_emoji_dict(spec.emoji)
            if emoji is not None:
                button["emoji"] = emoji
            buttons.append(button)
        rows.append({"type": _COMPONENT_TYPE_ACTION_ROW, "components": buttons})
    return rows


def build_select_components(
    select_custom_id: str, options_spec: list[SelectOptionSpec], placeholder: str
) -> list[dict]:
    """One action row containing one select menu, one option per binding."""
    options = [
        {"label": spec.label, "value": spec.value, "default": False} for spec in options_spec
    ]
    select: dict = {
        "type": _COMPONENT_TYPE_SELECT,
        "custom_id": select_custom_id,
        "min_values": 0,
        "max_values": len(options) if options else 1,
        "disabled": False,
        "required": False,
    }
    if placeholder:
        select["placeholder"] = placeholder
    if options:
        select["options"] = options
    return [{"type": _COMPONENT_TYPE_ACTION_ROW, "components": [select]}]
