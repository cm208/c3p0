"""Curated grouping of Discord's permission flags, for the role/channel-
overwrite permission pickers.

discord.py's `discord.Permissions.VALID_FLAGS` has 59 entries as of this
project's installed version, 6 of which are deprecated aliases sharing a
bit with a canonical name (read_messages=view_channel,
external_emojis=use_external_emojis, manage_permissions=manage_roles,
manage_emojis=manage_emojis_and_stickers,
external_stickers=use_external_stickers, send_polls=create_polls) - the 53
canonical names are grouped below. tests/web/test_permission_groups.py
asserts this stays exactly in sync with discord.py's canonical flag set, so
a future discord.py upgrade adding a flag fails loudly instead of silently
missing it from the UI.
"""

from __future__ import annotations

PERMISSION_GROUPS: dict[str, tuple[str, ...]] = {
    "General": (
        "view_channel", "view_audit_log", "view_guild_insights",
        "view_creator_monetization_analytics", "change_nickname",
        "manage_nicknames", "create_instant_invite", "use_application_commands",
    ),
    "Moderation": ("kick_members", "ban_members", "moderate_members", "manage_guild"),
    "Channel Management": (
        "manage_channels", "manage_roles", "manage_webhooks",
        "manage_threads", "create_public_threads",
        "create_private_threads", "set_voice_channel_status",
    ),
    "Messaging": (
        "send_messages", "send_messages_in_threads", "send_tts_messages",
        "embed_links", "attach_files", "add_reactions", "use_external_emojis",
        "use_external_stickers", "mention_everyone", "manage_messages",
        "pin_messages", "read_message_history", "bypass_slowmode", "create_polls",
    ),
    "Voice & Stage": (
        "connect", "speak", "stream", "mute_members", "deafen_members",
        "move_members", "use_voice_activation", "priority_speaker",
        "request_to_speak", "use_embedded_activities", "use_soundboard",
        "use_external_sounds", "send_voice_messages",
    ),
    "Events & Expressions": (
        "create_events", "manage_events", "create_expressions",
        "manage_expressions", "manage_emojis_and_stickers",
    ),
    "Advanced": ("administrator", "use_external_apps"),
}

# A smaller curated subset for the channel-overwrite editor specifically -
# the full role-picker set above is unnecessary for "who can see/post/
# moderate in this one channel".
CHANNEL_OVERWRITE_PERMISSIONS: tuple[str, ...] = (
    "view_channel", "send_messages", "read_message_history", "manage_messages",
    "mention_everyone", "embed_links", "attach_files", "use_external_emojis",
    "manage_channels", "manage_webhooks", "connect", "speak", "mute_members",
    "move_members", "use_voice_activation", "priority_speaker",
    "create_private_threads", "send_messages_in_threads",
)
