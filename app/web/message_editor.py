"""Context for the dashboard's message editor (app/web/static/message_editor.js).

Every textarea that holds text the bot will post into Discord (welcome
channel message/embed/DM, custom command responses, a reaction-role
message) is enhanced client-side into a toolbar + live preview. The JS
needs three things it can't derive from the page on its own, built here
once per request and embedded as a single JSON blob:

- `variables`: every `{placeholder}` the bot understands, with a label and
  a sample value for the preview. Each textarea then narrows this to the
  subset valid for *that* field via its own `data-variables` attribute
  (a welcome DM has no channel, a reaction-role message has no variables
  at all), so the full set is described exactly once, here.
- `roles`/`channels`: the guild's live roles/text channels, for the
  mention pickers and for resolving `<@&id>`/`<#id>` to names in the
  preview. Ids are strings, never JSON numbers - a snowflake routinely
  exceeds 2**53 and `JSON.parse` would silently round it (the exact bug
  that once made channels vanish from the channel canvas).
- `users`: id -> display name for user mentions the preview can resolve.
  Only the operator themself - `{user_mention}`'s sample value mentions
  them, so the preview reads as "what the new member would see" with a
  real name instead of an unresolved id.

Nothing here is trusted on submit: the editor only ever produces plain
text in the same textarea the form already posted, and every service
still validates that text exactly as before (e.g. `validate_template`).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.web.guild_options import GuildDiscordState
from app.web.sessions import LoadedSession


@dataclass(frozen=True, slots=True)
class EditorVariable:
    name: str
    label: str


# Display order and human labels for STANDARD_VARIABLES. Kept as an
# explicit tuple (not derived from the set) so the menu order is stable
# and deliberate; test_message_editor.py asserts it covers the set exactly.
EDITOR_VARIABLES: tuple[EditorVariable, ...] = (
    EditorVariable("user_mention", "Mention the member"),
    EditorVariable("user", "Member's name"),
    EditorVariable("user_id", "Member's ID"),
    EditorVariable("server", "Server name"),
    EditorVariable("member_count", "Member count"),
    EditorVariable("count", "Member count (short form)"),
    EditorVariable("channel_mention", "Mention the channel"),
    EditorVariable("channel", "Channel name"),
    EditorVariable("uptime", "Bot uptime"),
)

# Named subsets, referenced by each textarea's data-variables attribute.
# A welcome DM renders with no channel (app/cogs/welcome.py's _send_dm
# passes None), so {channel}/{channel_mention} would always come out empty
# there - validation still accepts them, the editor just doesn't offer them.
ALL_VARIABLES = ",".join(v.name for v in EDITOR_VARIABLES)
DM_VARIABLES = ",".join(v.name for v in EDITOR_VARIABLES if not v.name.startswith("channel"))

# Illustrative only - the web process has no gateway connection, so no
# live member count is available without an extra REST call per page load.
_SAMPLE_MEMBER_COUNT = "128"
_SAMPLE_UPTIME = "3d 4h"


def build_editor_context(
    *,
    state: GuildDiscordState,
    session: LoadedSession,
    guild_name: str | None,
    sample_channel_id: int | None = None,
) -> dict:
    """The JSON-serializable blob message_editor.js reads on page load.

    `sample_channel_id` picks which channel `{channel}`/`{channel_mention}`
    preview as (e.g. the configured welcome channel); falls back to the
    first text channel, then to a placeholder name.
    """
    channels = {c.id: c.name for c in state.text_channels}
    if sample_channel_id not in channels:
        sample_channel_id = next(iter(channels), None)
    if sample_channel_id is not None:
        channel_name = channels[sample_channel_id]
        channel_mention = f"<#{sample_channel_id}>"
    else:
        channel_name = channel_mention = "#general"

    user_id = session.discord_user_id
    # discord_username is nullable on a session row; never render "None".
    username = session.discord_username or "new-member"
    samples = {
        "user": username,
        "user_mention": f"<@{user_id}>",
        "user_id": str(user_id),
        "server": guild_name or "Your Server",
        "member_count": _SAMPLE_MEMBER_COUNT,
        "count": _SAMPLE_MEMBER_COUNT,
        "uptime": _SAMPLE_UPTIME,
        "channel": channel_name,
        "channel_mention": channel_mention,
    }

    return {
        "variables": [
            {"name": v.name, "label": v.label, "sample": samples[v.name]} for v in EDITOR_VARIABLES
        ],
        "roles": [{"id": str(r.id), "name": r.name} for r in state.all_roles],
        "channels": [{"id": str(c.id), "name": c.name} for c in state.text_channels],
        "users": {str(user_id): username},
    }
