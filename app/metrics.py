"""Prometheus metrics.

Labels are kept low-cardinality by design: never label by user ID, message
ID, guild ID, or arbitrary command text. Command names are fine since they
come from a fixed, known set defined by the bot itself.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Info, start_http_server

# --- Process / connection health ---

BOT_INFO = Info("c3p0_bot", "Static build/version information")

BOT_UP = Gauge(
    "c3p0_bot_up",
    "Whether the bot process is up (1) or not (0). Set once the process starts.",
)

DISCORD_CONNECTED = Gauge(
    "c3p0_discord_connected",
    "Whether the bot currently has a live Discord gateway connection (1) or not (0).",
)

GUILD_COUNT = Gauge(
    "c3p0_guild_count",
    "Number of guilds the bot is currently a member of.",
)

# --- Commands ---

COMMANDS_INVOKED = Counter(
    "c3p0_commands_invoked_total",
    "Number of commands invoked, by command name and category.",
    ["command", "category"],
)

COMMAND_FAILURES = Counter(
    "c3p0_command_failures_total",
    "Number of command invocations that resulted in an error.",
    ["command", "category"],
)

# --- Moderation ---

MODERATION_ACTIONS = Counter(
    "c3p0_moderation_actions_total",
    "Number of moderation actions taken, by action type.",
    ["action"],
)

# --- Membership ---

MEMBER_JOINS = Counter("c3p0_member_joins_total", "Number of member-join events observed.")
MEMBER_LEAVES = Counter("c3p0_member_leaves_total", "Number of member-leave events observed.")

# --- Music ---

MUSIC_PLAYS = Counter("c3p0_music_plays_total", "Number of successful track playback starts.")
MUSIC_FAILURES = Counter(
    "c3p0_music_failures_total",
    "Number of music playback failures, by reason.",
    ["reason"],
)
MUSIC_QUEUE_LENGTH = Gauge(
    "c3p0_music_queue_length",
    "Current music queue length for a guild player.",
    ["guild_id"],
)

# --- Custom commands ---

CUSTOM_COMMAND_INVOCATIONS = Counter(
    "c3p0_custom_command_invocations_total",
    "Number of custom command invocations across all guilds.",
)

# --- Database ---

DATABASE_OPERATION_FAILURES = Counter(
    "c3p0_database_operation_failures_total",
    "Number of database operations that raised an error, by operation.",
    ["operation"],
)


def start_metrics_server(host: str, port: int, version: str) -> None:
    """Start the Prometheus HTTP exposition server.

    This is fire-and-forget: prometheus_client spins up a background thread
    serving /metrics on the given host/port.
    """
    BOT_INFO.info({"version": version})
    start_http_server(port=port, addr=host)
    BOT_UP.set(1)
