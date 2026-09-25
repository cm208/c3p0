"""Safe, explicit-variable-map template substitution.

Used anywhere user-supplied text has `{variable}`-style placeholders filled
in (welcome messages, and custom commands - both use the identical
variable set and the same "explicit variable map, no arbitrary expression
evaluation" approach).

Deliberately NOT built on str.format()/str.format_map(): those resolve
`{x.attr}` and `{x[key]}` against real objects and support format specs,
which is more surface area than "substitute one of a fixed set of plain
strings" needs. A plain regex on bare `{word}` tokens can't do attribute
access, indexing, or code execution - it only ever produces a dict lookup
or leaves the text untouched.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# The fixed variable set both welcome messages and custom commands render
# against - {user}, {user_mention}, {user_id}, {server}, {member_count}
# (and its short alias {count}), {channel}, {channel_mention}, {uptime}.
STANDARD_VARIABLES = {
    "user",
    "user_mention",
    "user_id",
    "server",
    "member_count",
    "count",
    "channel",
    "channel_mention",
    "uptime",
}


@dataclass(frozen=True, slots=True)
class TemplateContext:
    """Plain-data inputs shared by every STANDARD_VARIABLES-rendering feature.

    No discord.py types - the cog translates a discord.Member/Guild/
    TextChannel into this before handing it to a service.
    """

    user_display_name: str
    user_mention: str
    user_id: int
    guild_name: str
    member_count: int
    channel_name: str = ""
    channel_mention: str = ""
    # Pre-formatted by format_uptime(); empty when the caller has no bot
    # start time to measure from.
    uptime: str = ""


def context_variables(context: TemplateContext) -> dict[str, str]:
    return {
        "user": context.user_display_name,
        "user_mention": context.user_mention,
        "user_id": str(context.user_id),
        "server": context.guild_name,
        "member_count": str(context.member_count),
        "count": str(context.member_count),
        "channel": context.channel_name,
        "channel_mention": context.channel_mention,
        "uptime": context.uptime,
    }


def format_uptime(seconds: float) -> str:
    """Compact uptime for templates and the dashboard: "14d 6h", "3h 12m", "5m"."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class UnknownTemplateVariableError(Exception):
    """Raised when a template references a variable outside the allowed set.

    Message is user-safe.
    """


def validate_template(template: str, allowed_variables: Mapping[str, str] | set[str]) -> None:
    """Raise if `template` references any `{placeholder}` not in `allowed_variables`."""
    allowed = set(allowed_variables)
    unknown = sorted({name for name in _PLACEHOLDER_RE.findall(template) if name not in allowed})
    if unknown:
        joined = ", ".join(f"{{{name}}}" for name in unknown)
        raise UnknownTemplateVariableError(f"Unknown template variable(s): {joined}")


def render_template(template: str, variables: Mapping[str, str]) -> str:
    """Replace every `{key}` found in `variables` with its value.

    Any `{word}` not present in `variables` is left as literal text rather
    than raising, since templates are validated up front at configuration
    time (see `validate_template`) - this stays defensive at render time
    rather than risking a crash during a live event handler.
    """

    def _replace(match: re.Match[str]) -> str:
        return variables.get(match.group(1), match.group(0))

    return _PLACEHOLDER_RE.sub(_replace, template)


def render_template_context(template: str, context: TemplateContext) -> str:
    """Convenience: render `template` against a TemplateContext's standard variables."""
    return render_template(template, context_variables(context))


def uptime_since(started_monotonic: float | None) -> str:
    """format_uptime() measured from a time.monotonic() start, or "" when
    there's no start time (e.g. a test double standing in for the bot)."""
    if started_monotonic is None:
        return ""
    return format_uptime(time.monotonic() - started_monotonic)
