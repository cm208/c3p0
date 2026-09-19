"""Runtime/deployment configuration.

Per the project's configuration philosophy, environment variables are
reserved for deployment and infrastructure settings (secrets, database
location, metrics port, log level, etc). Routine per-guild server settings
(welcome channel, prefix, moderation thresholds, ...) live in SQLite and are
managed from Discord - they never belong here.

`.env` is only loaded for local development convenience. In production,
environment variables should be provided by the process manager / Docker
Compose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from app.utils.env import EnvError, get_bool, get_int, get_int_list

# Loading .env is a no-op if the file doesn't exist, so this is safe in
# production containers that supply real environment variables directly.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str
    discord_application_id: int | None
    internal_api_token: str
    dev_guild_ids: list[int] = field(default_factory=list)

    database_url: str = "sqlite+aiosqlite:////data/c3p0.db"

    metrics_host: str = "0.0.0.0"  # noqa: S104 - intentional container bind
    metrics_port: int = 8000

    # Bot-internal control-plane API (live music state/controls for the web
    # dashboard - see app/music/internal_api.py). Deliberately never
    # published in compose.yaml; only c3p0-web calls it, over the Compose
    # network by service name.
    internal_api_host: str = "0.0.0.0"  # noqa: S104 - intentional container bind
    internal_api_port: int = 8100

    log_level: str = "INFO"
    log_human: bool = False

    default_prefix: str = "!"

    def __repr__(self) -> str:  # pragma: no cover - defensive redaction
        # Never let the token leak into logs/tracebacks via repr().
        return (
            "Config(discord_token='***redacted***', "
            f"discord_application_id={self.discord_application_id!r}, "
            "internal_api_token='***redacted***', "
            f"dev_guild_ids={self.dev_guild_ids!r}, "
            f"database_url={self.database_url!r}, "
            f"metrics_host={self.metrics_host!r}, metrics_port={self.metrics_port!r}, "
            f"internal_api_host={self.internal_api_host!r}, "
            f"internal_api_port={self.internal_api_port!r}, "
            f"log_level={self.log_level!r}, log_human={self.log_human!r}, "
            f"default_prefix={self.default_prefix!r})"
        )


def load_config() -> Config:
    """Load and validate configuration from environment variables.

    Raises:
        ConfigError: if required configuration is missing or malformed.
    """
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and set a real bot token."
        )

    app_id_raw = os.environ.get("DISCORD_APPLICATION_ID", "").strip()
    app_id = int(app_id_raw) if app_id_raw else None

    internal_api_token = os.environ.get("INTERNAL_API_TOKEN", "").strip()
    if not internal_api_token:
        raise ConfigError(
            "INTERNAL_API_TOKEN is not set. Generate one (e.g. `openssl rand -hex 32`) and set "
            "the same value for both the c3p0 and c3p0-web services."
        )

    try:
        return Config(
            discord_token=token,
            discord_application_id=app_id,
            internal_api_token=internal_api_token,
            dev_guild_ids=get_int_list("DISCORD_DEV_GUILD_IDS"),
            database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:////data/c3p0.db"),
            metrics_host=os.environ.get("METRICS_HOST", "0.0.0.0"),  # noqa: S104
            metrics_port=get_int("METRICS_PORT", 8000),
            internal_api_host=os.environ.get("INTERNAL_API_HOST", "0.0.0.0"),  # noqa: S104
            internal_api_port=get_int("INTERNAL_API_PORT", 8100),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            log_human=get_bool("LOG_HUMAN", False),
            default_prefix=os.environ.get("DEFAULT_PREFIX", "!"),
        )
    except EnvError as exc:
        raise ConfigError(str(exc)) from exc
