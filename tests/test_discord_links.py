from __future__ import annotations

import pytest

from app.utils.discord_links import InvalidMessageLinkError, parse_message_link


def test_parse_message_link_basic() -> None:
    guild_id, channel_id, message_id = parse_message_link(
        "https://discord.com/channels/111/222/333"
    )

    assert (guild_id, channel_id, message_id) == (111, 222, 333)


def test_parse_message_link_accepts_canary_and_ptb() -> None:
    assert parse_message_link("https://canary.discord.com/channels/1/2/3") == (1, 2, 3)
    assert parse_message_link("https://ptb.discord.com/channels/1/2/3") == (1, 2, 3)


def test_parse_message_link_accepts_bare_domain_without_scheme() -> None:
    assert parse_message_link("discord.com/channels/1/2/3") == (1, 2, 3)


def test_parse_message_link_strips_surrounding_whitespace() -> None:
    assert parse_message_link("  https://discord.com/channels/1/2/3  ") == (1, 2, 3)


def test_parse_message_link_rejects_garbage() -> None:
    with pytest.raises(InvalidMessageLinkError):
        parse_message_link("not a link")


def test_parse_message_link_rejects_wrong_domain() -> None:
    with pytest.raises(InvalidMessageLinkError):
        parse_message_link("https://evil.example.com/channels/1/2/3")


def test_parse_message_link_rejects_spoofed_prefix_domain() -> None:
    # "notdiscord.com" contains "discord.com" as a trailing substring - must
    # not be accepted as a real Discord domain.
    with pytest.raises(InvalidMessageLinkError):
        parse_message_link("https://notdiscord.com/channels/1/2/3")


def test_parse_message_link_rejects_spoofed_subdomain() -> None:
    with pytest.raises(InvalidMessageLinkError):
        parse_message_link("https://evil.discord.com/channels/1/2/3")
