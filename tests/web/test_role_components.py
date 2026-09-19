from __future__ import annotations

from app.utils.emoji import normalize_emoji
from app.web.role_components import (
    ButtonSpec,
    SelectOptionSpec,
    build_button_components,
    build_select_components,
    button_emoji_dict,
    reaction_path_emoji,
)


def test_reaction_path_emoji_unicode_passthrough() -> None:
    assert reaction_path_emoji("🎮") == "🎮"


def test_reaction_path_emoji_static_custom_strips_angle_brackets_only() -> None:
    # Deliberately NOT the cleaner "name:id" form - matches discord.py's
    # own convert_emoji_reaction() str-branch exactly (leading ':' retained).
    normalized = normalize_emoji("<:catjam:123456789012345678>")
    assert reaction_path_emoji(normalized) == ":catjam:123456789012345678"


def test_reaction_path_emoji_animated_custom_strips_angle_brackets_only() -> None:
    normalized = normalize_emoji("<a:party:987654321098765432>")
    assert reaction_path_emoji(normalized) == "a:party:987654321098765432"


def test_button_emoji_dict_none_for_no_emoji() -> None:
    assert button_emoji_dict(None) is None


def test_button_emoji_dict_unicode() -> None:
    result = button_emoji_dict("🎮")
    assert result == {"id": None, "name": "🎮"}


def test_button_emoji_dict_static_custom() -> None:
    normalized = normalize_emoji("<:catjam:123456789012345678>")
    result = button_emoji_dict(normalized)
    assert result == {"id": 123456789012345678, "name": "catjam"}


def test_button_emoji_dict_animated_custom_includes_animated_flag() -> None:
    normalized = normalize_emoji("<a:party:987654321098765432>")
    result = button_emoji_dict(normalized)
    assert result == {"id": 987654321098765432, "name": "party", "animated": True}


def test_build_button_components_single_button_single_row() -> None:
    specs = [ButtonSpec(label="Gamer", custom_id="c3p0:rolebtn:abc", emoji=None)]

    rows = build_button_components(specs)

    assert rows == [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "disabled": False,
                    "label": "Gamer",
                    "custom_id": "c3p0:rolebtn:abc",
                }
            ],
        }
    ]


def test_build_button_components_includes_emoji_when_present() -> None:
    specs = [ButtonSpec(label="Gamer", custom_id="c3p0:rolebtn:abc", emoji="🎮")]

    rows = build_button_components(specs)

    assert rows[0]["components"][0]["emoji"] == {"id": None, "name": "🎮"}


def test_build_button_components_chunks_at_five_per_row() -> None:
    specs = [ButtonSpec(label=f"Role{i}", custom_id=f"c3p0:rolebtn:{i}", emoji=None) for i in range(7)]

    rows = build_button_components(specs)

    assert len(rows) == 2
    assert len(rows[0]["components"]) == 5
    assert len(rows[1]["components"]) == 2
    assert rows[0]["components"][0]["label"] == "Role0"
    assert rows[1]["components"][0]["label"] == "Role5"


def test_build_button_components_empty_list_yields_no_rows() -> None:
    assert build_button_components([]) == []


def test_build_select_components_shape() -> None:
    options = [SelectOptionSpec(label="Gamer", value="1"), SelectOptionSpec(label="Artist", value="2")]

    rows = build_select_components("c3p0:roleselect:555", options, "Select roles...")

    assert rows == [
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": "c3p0:roleselect:555",
                    "min_values": 0,
                    "max_values": 2,
                    "disabled": False,
                    "required": False,
                    "placeholder": "Select roles...",
                    "options": [
                        {"label": "Gamer", "value": "1", "default": False},
                        {"label": "Artist", "value": "2", "default": False},
                    ],
                }
            ],
        }
    ]


def test_build_select_components_omits_placeholder_when_empty() -> None:
    rows = build_select_components("c3p0:roleselect:555", [SelectOptionSpec("Gamer", "1")], "")

    assert "placeholder" not in rows[0]["components"][0]


def test_build_select_components_no_options_still_valid_shape() -> None:
    rows = build_select_components("c3p0:roleselect:555", [], "Select roles...")

    select = rows[0]["components"][0]
    assert "options" not in select
    assert select["max_values"] == 1
