"""Built-in starter template catalog.

Plain Python data, not database rows - keeping starter-template *content*
as a normal code change/review is simpler than coupling it to append-only
migration data, and TemplateService.list_catalog merges this catalog with
a guild's own saved rows at read time.

Deliberately no `import discord` here (or anywhere else in app/services/) -
the only places allowed to touch discord.py directly are app/music/*.py
(a live audio player is inherently tied to a discord.VoiceClient) and
app/web/discord_client.py/role_components.py (the web process's own
hand-rolled REST calls), and this isn't a third. The permission-bit constants
below are plain integers, verified once against this project's installed
discord.py (`discord.Permissions(view_channel=True).value` etc.) rather than
computed via a discord.py import at runtime.

Role/channel/overwrite references are by *name*, never by id - ids don't
exist yet on whatever guild a template is later applied to.
`TemplateOverwriteDef.role_name` may be the special sentinel "@everyone",
resolved by the applier to the guild's default role (whose id always equals
the guild's own id) rather than any role in `TemplateDefinition.roles`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EVERYONE_ROLE_NAME = "@everyone"

# Discord channel type ids (https://discord.com/developers/docs/resources/channel).
TEXT_CHANNEL_TYPE = 0
VOICE_CHANNEL_TYPE = 2
CATEGORY_CHANNEL_TYPE = 4

# Verified against this project's installed discord.py:
# discord.Permissions(<name>=True).value for each flag below.
_VIEW_CHANNEL = 0x400
_KICK_MEMBERS = 0x2
_BAN_MEMBERS = 0x4
_MANAGE_MESSAGES = 0x2000
_MANAGE_CHANNELS = 0x10
_MANAGE_NICKNAMES = 0x8000000

_MODERATOR_PERMISSIONS = _KICK_MEMBERS | _BAN_MEMBERS | _MANAGE_MESSAGES | _MANAGE_NICKNAMES
_ORGANIZER_PERMISSIONS = _MANAGE_MESSAGES | _MANAGE_CHANNELS | _KICK_MEMBERS


@dataclass(frozen=True, slots=True)
class TemplateRoleDef:
    name: str
    color: int = 0
    hoist: bool = False
    mentionable: bool = False
    permissions: int = 0


@dataclass(frozen=True, slots=True)
class TemplateOverwriteDef:
    role_name: str  # EVERYONE_ROLE_NAME or a `name` from this template's own roles
    allow: int = 0
    deny: int = 0


@dataclass(frozen=True, slots=True)
class TemplateChannelDef:
    name: str
    type: int  # TEXT_CHANNEL_TYPE, VOICE_CHANNEL_TYPE, or CATEGORY_CHANNEL_TYPE
    parent_name: str | None = None  # must name a CATEGORY_CHANNEL_TYPE def in the same template
    topic: str | None = None
    nsfw: bool = False
    rate_limit_per_user: int = 0
    bitrate: int | None = None
    user_limit: int | None = None
    overwrites: tuple[TemplateOverwriteDef, ...] = ()


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    roles: tuple[TemplateRoleDef, ...] = field(default_factory=tuple)
    channels: tuple[TemplateChannelDef, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class BuiltinTemplate:
    slug: str
    name: str
    description: str
    definition: TemplateDefinition


def _community_starter() -> TemplateDefinition:
    moderator = TemplateRoleDef(name="Moderator", hoist=True, permissions=_MODERATOR_PERMISSIONS)
    staff_only = (
        TemplateOverwriteDef(role_name=EVERYONE_ROLE_NAME, deny=_VIEW_CHANNEL),
        TemplateOverwriteDef(role_name="Moderator", allow=_VIEW_CHANNEL),
    )
    return TemplateDefinition(
        roles=(moderator,),
        channels=(
            TemplateChannelDef(name="Information", type=CATEGORY_CHANNEL_TYPE),
            TemplateChannelDef(name="welcome", type=TEXT_CHANNEL_TYPE, parent_name="Information"),
            TemplateChannelDef(name="rules", type=TEXT_CHANNEL_TYPE, parent_name="Information"),
            TemplateChannelDef(name="announcements", type=TEXT_CHANNEL_TYPE, parent_name="Information"),
            TemplateChannelDef(name="General", type=CATEGORY_CHANNEL_TYPE),
            TemplateChannelDef(name="general", type=TEXT_CHANNEL_TYPE, parent_name="General"),
            TemplateChannelDef(name="off-topic", type=TEXT_CHANNEL_TYPE, parent_name="General"),
            TemplateChannelDef(name="General Voice", type=VOICE_CHANNEL_TYPE, parent_name="General"),
            TemplateChannelDef(name="Staff", type=CATEGORY_CHANNEL_TYPE, overwrites=staff_only),
            TemplateChannelDef(
                name="mod-chat", type=TEXT_CHANNEL_TYPE, parent_name="Staff", overwrites=staff_only
            ),
            TemplateChannelDef(
                name="Mod Voice", type=VOICE_CHANNEL_TYPE, parent_name="Staff", overwrites=staff_only
            ),
        ),
    )


def _gaming_clan() -> TemplateDefinition:
    officer = TemplateRoleDef(name="Officer", hoist=True, permissions=_MODERATOR_PERMISSIONS)
    officers_only = (
        TemplateOverwriteDef(role_name=EVERYONE_ROLE_NAME, deny=_VIEW_CHANNEL),
        TemplateOverwriteDef(role_name="Officer", allow=_VIEW_CHANNEL),
    )
    return TemplateDefinition(
        roles=(officer,),
        channels=(
            TemplateChannelDef(name="Info", type=CATEGORY_CHANNEL_TYPE),
            TemplateChannelDef(name="announcements", type=TEXT_CHANNEL_TYPE, parent_name="Info"),
            TemplateChannelDef(name="rules", type=TEXT_CHANNEL_TYPE, parent_name="Info"),
            TemplateChannelDef(name="Community", type=CATEGORY_CHANNEL_TYPE),
            TemplateChannelDef(name="general-chat", type=TEXT_CHANNEL_TYPE, parent_name="Community"),
            TemplateChannelDef(name="clips-and-memes", type=TEXT_CHANNEL_TYPE, parent_name="Community"),
            TemplateChannelDef(name="Gaming Voice 1", type=VOICE_CHANNEL_TYPE, parent_name="Community"),
            TemplateChannelDef(name="Gaming Voice 2", type=VOICE_CHANNEL_TYPE, parent_name="Community"),
            TemplateChannelDef(name="Officers", type=CATEGORY_CHANNEL_TYPE, overwrites=officers_only),
            TemplateChannelDef(
                name="officer-chat", type=TEXT_CHANNEL_TYPE, parent_name="Officers", overwrites=officers_only
            ),
            TemplateChannelDef(
                name="Officer Voice", type=VOICE_CHANNEL_TYPE, parent_name="Officers", overwrites=officers_only
            ),
        ),
    )


def _study_group() -> TemplateDefinition:
    organizer = TemplateRoleDef(name="Organizer", hoist=True, permissions=_ORGANIZER_PERMISSIONS)
    return TemplateDefinition(
        roles=(organizer,),
        channels=(
            TemplateChannelDef(name="General", type=CATEGORY_CHANNEL_TYPE),
            TemplateChannelDef(name="announcements", type=TEXT_CHANNEL_TYPE, parent_name="General"),
            TemplateChannelDef(name="general-chat", type=TEXT_CHANNEL_TYPE, parent_name="General"),
            TemplateChannelDef(
                name="resources", type=TEXT_CHANNEL_TYPE, parent_name="General",
                topic="Shared notes, links, and files.",
            ),
            TemplateChannelDef(name="Study Rooms", type=CATEGORY_CHANNEL_TYPE),
            TemplateChannelDef(name="Study Room 1", type=VOICE_CHANNEL_TYPE, parent_name="Study Rooms"),
            TemplateChannelDef(name="Study Room 2", type=VOICE_CHANNEL_TYPE, parent_name="Study Rooms"),
            TemplateChannelDef(name="Focus Room", type=VOICE_CHANNEL_TYPE, parent_name="Study Rooms", user_limit=1),
        ),
    )


BUILTIN_TEMPLATES: tuple[BuiltinTemplate, ...] = (
    BuiltinTemplate(
        slug="community-starter",
        name="Community Starter",
        description="A general-purpose layout: welcome/rules/announcements, general chat and voice, and a private staff area.",
        definition=_community_starter(),
    ),
    BuiltinTemplate(
        slug="gaming-clan",
        name="Gaming Clan",
        description="Community + voice channels for a gaming group, plus a private officers-only area.",
        definition=_gaming_clan(),
    ),
    BuiltinTemplate(
        slug="study-group",
        name="Study / Work Group",
        description="Announcements, a resources channel, and dedicated study/focus voice rooms.",
        definition=_study_group(),
    ),
)


def get_builtin(slug: str) -> BuiltinTemplate | None:
    for template in BUILTIN_TEMPLATES:
        if template.slug == slug:
            return template
    return None


def definition_to_dict(definition: TemplateDefinition) -> dict:
    """JSON-friendly serialization for storage in GuildTemplate.definition."""
    return {
        "roles": [
            {"name": r.name, "color": r.color, "hoist": r.hoist, "mentionable": r.mentionable, "permissions": r.permissions}
            for r in definition.roles
        ],
        "channels": [
            {
                "name": c.name,
                "type": c.type,
                "parent_name": c.parent_name,
                "topic": c.topic,
                "nsfw": c.nsfw,
                "rate_limit_per_user": c.rate_limit_per_user,
                "bitrate": c.bitrate,
                "user_limit": c.user_limit,
                "overwrites": [
                    {"role_name": o.role_name, "allow": o.allow, "deny": o.deny} for o in c.overwrites
                ],
            }
            for c in definition.channels
        ],
    }


def definition_from_dict(data: dict) -> TemplateDefinition:
    """Inverse of definition_to_dict - reconstructs the nested dataclasses."""
    roles = tuple(TemplateRoleDef(**r) for r in data.get("roles", []))
    channels = []
    for raw in data.get("channels", []):
        raw = dict(raw)
        overwrites = tuple(TemplateOverwriteDef(**o) for o in raw.pop("overwrites", []))
        channels.append(TemplateChannelDef(**raw, overwrites=overwrites))
    return TemplateDefinition(roles=roles, channels=tuple(channels))
