from __future__ import annotations

import pytest

from app.utils.templates import (
    STANDARD_VARIABLES,
    TemplateContext,
    UnknownTemplateVariableError,
    context_variables,
    format_uptime,
    render_template,
    render_template_context,
    uptime_since,
    validate_template,
)


def test_render_template_substitutes_known_variables() -> None:
    result = render_template(
        "Welcome {user_mention} to {server}!", {"user_mention": "<@1>", "server": "Test"}
    )

    assert result == "Welcome <@1> to Test!"


def test_render_template_leaves_unknown_placeholder_literal() -> None:
    result = render_template("Hi {typo}", {"user": "Alice"})

    assert result == "Hi {typo}"


def test_render_template_ignores_attribute_and_format_spec_syntax() -> None:
    # Not str.format()-based: dotted/indexed/format-spec syntax never
    # matches the plain `{word}` pattern, so it's left untouched rather
    # than resolved against the value's attributes.
    result = render_template("{user.__class__} {user:>10}", {"user": "Alice"})

    assert result == "{user.__class__} {user:>10}"


def test_validate_template_accepts_known_variables() -> None:
    validate_template("Welcome {user} to {server}", {"user", "server"})


def test_validate_template_rejects_unknown_variable() -> None:
    with pytest.raises(UnknownTemplateVariableError, match=r"\{typo\}"):
        validate_template("Welcome {user} {typo}", {"user"})


def test_validate_template_reports_all_unknown_variables_sorted() -> None:
    with pytest.raises(UnknownTemplateVariableError, match=r"\{alpha\}, \{zulu\}"):
        validate_template("{zulu} {alpha} {user}", {"user"})


def test_context_variables_maps_all_standard_keys() -> None:
    context = TemplateContext(
        user_display_name="Alice",
        user_mention="<@1>",
        user_id=1,
        guild_name="Test Server",
        member_count=42,
        channel_name="general",
        channel_mention="<#2>",
    )

    variables = context_variables(context)

    assert set(variables) == STANDARD_VARIABLES
    assert variables["user"] == "Alice"
    assert variables["user_id"] == "1"
    assert variables["member_count"] == "42"


def test_context_variables_defaults_channel_fields_to_empty() -> None:
    context = TemplateContext(
        user_display_name="Alice", user_mention="<@1>", user_id=1, guild_name="Test", member_count=1
    )

    variables = context_variables(context)

    assert variables["channel"] == ""
    assert variables["channel_mention"] == ""


def test_render_template_context_combines_both_steps() -> None:
    context = TemplateContext(
        user_display_name="Alice", user_mention="<@1>", user_id=1, guild_name="Test Server", member_count=5
    )

    rendered = render_template_context("Hi {user}, welcome to {server}!", context)

    assert rendered == "Hi Alice, welcome to Test Server!"


def test_count_is_an_alias_of_member_count_and_uptime_is_passed_through() -> None:
    context = TemplateContext(
        user_display_name="Alice", user_mention="<@1>", user_id=1, guild_name="G", member_count=42, uptime="3d 4h"
    )

    assert render_template_context("#{count} of {member_count}, up {uptime}", context) == "#42 of 42, up 3d 4h"
    assert {"count", "uptime"} <= STANDARD_VARIABLES


def test_format_uptime_picks_the_two_largest_units() -> None:
    assert format_uptime(14 * 86400 + 6 * 3600 + 59) == "14d 6h"
    assert format_uptime(3 * 3600 + 12 * 60) == "3h 12m"
    assert format_uptime(59) == "0m"
    assert format_uptime(-5) == "0m"


def test_uptime_since_is_empty_without_a_start_time() -> None:
    assert uptime_since(None) == ""
