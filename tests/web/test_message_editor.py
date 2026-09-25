from __future__ import annotations

from app.utils.templates import STANDARD_VARIABLES
from app.web.discord_client import DiscordChannel, DiscordRole
from app.web.guild_options import GuildDiscordState
from app.web.message_editor import (
    ALL_VARIABLES,
    DM_VARIABLES,
    EDITOR_VARIABLES,
    build_editor_context,
)
from app.web.sessions import LoadedSession

# Past 2**53 - would be silently rounded if ever emitted as a JSON number.
BIG_ROLE_ID = 1234567890123456789
BIG_CHANNEL_ID = 1234567890123456791
OTHER_CHANNEL_ID = 600


def _make_session(username: str | None = "volvo") -> LoadedSession:
    return LoadedSession(
        id=1,
        discord_user_id=42,
        discord_username=username,
        discord_avatar_hash=None,
        csrf_token="csrf",
        guild_permissions={},
    )


def _make_state(channels: list[DiscordChannel] | None = None) -> GuildDiscordState:
    role = DiscordRole(id=BIG_ROLE_ID, name="Member", position=1, managed=False)
    if channels is None:
        channels = [
            DiscordChannel(id=OTHER_CHANNEL_ID, name="general", type=0),
            DiscordChannel(id=BIG_CHANNEL_ID, name="welcome", type=0),
        ]
    return GuildDiscordState(all_roles=[role], assignable_roles=[role], text_channels=channels)


def test_editor_variables_cover_the_standard_set_exactly() -> None:
    assert {v.name for v in EDITOR_VARIABLES} == STANDARD_VARIABLES
    assert set(ALL_VARIABLES.split(",")) == STANDARD_VARIABLES
    assert set(DM_VARIABLES.split(",")) == STANDARD_VARIABLES - {"channel", "channel_mention"}


def test_ids_are_strings_and_survive_past_float_precision() -> None:
    ctx = build_editor_context(state=_make_state(), session=_make_session(), guild_name="Guild A")

    assert ctx["roles"] == [{"id": str(BIG_ROLE_ID), "name": "Member"}]
    assert {"id": str(BIG_CHANNEL_ID), "name": "welcome"} in ctx["channels"]
    assert ctx["users"] == {"42": "volvo"}


def test_samples_use_operator_guild_and_requested_channel() -> None:
    ctx = build_editor_context(
        state=_make_state(), session=_make_session(), guild_name="Guild A", sample_channel_id=BIG_CHANNEL_ID
    )
    samples = {v["name"]: v["sample"] for v in ctx["variables"]}

    assert samples["user"] == "volvo"
    assert samples["user_mention"] == "<@42>"
    assert samples["server"] == "Guild A"
    assert samples["channel"] == "welcome"
    assert samples["channel_mention"] == f"<#{BIG_CHANNEL_ID}>"


def test_sample_channel_falls_back_to_first_then_placeholder() -> None:
    stale = build_editor_context(
        state=_make_state(), session=_make_session(), guild_name="Guild A", sample_channel_id=999
    )
    none_available = build_editor_context(
        state=_make_state(channels=[]), session=_make_session(), guild_name=None
    )

    stale_samples = {v["name"]: v["sample"] for v in stale["variables"]}
    empty_samples = {v["name"]: v["sample"] for v in none_available["variables"]}
    assert stale_samples["channel_mention"] == f"<#{OTHER_CHANNEL_ID}>"
    assert empty_samples["channel"] == "#general"
    assert empty_samples["server"] == "Your Server"


def test_missing_username_never_renders_none() -> None:
    ctx = build_editor_context(state=_make_state(), session=_make_session(username=None), guild_name="G")
    samples = {v["name"]: v["sample"] for v in ctx["variables"]}

    assert samples["user"] == "new-member"
    assert ctx["users"] == {"42": "new-member"}
