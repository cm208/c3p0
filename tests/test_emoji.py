from __future__ import annotations

import pytest

from app.utils.emoji import InvalidEmojiError, normalize_emoji


def test_normalize_unicode_emoji_passes_through() -> None:
    assert normalize_emoji("🎮") == "🎮"


def test_normalize_custom_emoji_markup() -> None:
    assert normalize_emoji("<:gamer:123456789012345678>") == "<:gamer:123456789012345678>"


def test_normalize_animated_custom_emoji_markup() -> None:
    assert normalize_emoji("<a:party:123456789012345678>") == "<a:party:123456789012345678>"


def test_normalize_strips_whitespace() -> None:
    assert normalize_emoji("  🎮  ") == "🎮"


def test_normalize_rejects_empty() -> None:
    with pytest.raises(InvalidEmojiError, match="empty"):
        normalize_emoji("   ")
