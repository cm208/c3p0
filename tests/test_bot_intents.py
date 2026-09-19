from __future__ import annotations

from app.bot import REQUIRED_INTENTS


def test_guild_messages_intent_is_enabled() -> None:
    """Regression test.

    message_content only populates content on messages the bot already
    receives - guild_messages is the separate intent that makes Discord
    deliver MESSAGE_CREATE events for guild channels at all. Without it,
    on_message never fires for real user messages, silently killing every
    `!`-prefixed command and the custom-commands chat trigger, while slash
    commands (a separate interaction event) keep working fine - which is
    exactly what made this easy to ship without noticing.
    """
    assert REQUIRED_INTENTS.guild_messages is True


def test_message_content_intent_is_enabled() -> None:
    assert REQUIRED_INTENTS.message_content is True
