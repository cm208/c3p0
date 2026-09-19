"""Web dashboard runtime configuration.

Mirrors app/config.py's shape and philosophy: environment variables only,
reused parsing helpers from app.utils.env, same ConfigError contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from app.config import ConfigError
from app.utils.env import EnvError, get_bool, get_int

load_dotenv()


@dataclass(frozen=True, slots=True)
class WebConfig:
    discord_client_id: int
    discord_client_secret: str
    discord_bot_token: str
    public_base_url: str
    internal_api_token: str

    database_url: str = "sqlite+aiosqlite:////data/c3p0.db"

    web_host: str = "0.0.0.0"  # noqa: S104 - intentional container bind
    web_port: int = 8080

    # Bot-internal control-plane API (live music state/controls) - reachable
    # by Compose service name, never published to the host. See
    # app/music/internal_api.py.
    bot_internal_base_url: str = "http://c3p0:8100"

    log_level: str = "INFO"
    log_human: bool = False

    cookie_secure: bool = True
    session_ttl_days: int = 30
    guild_permissions_cache_ttl_seconds: int = 300

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/auth/callback"

    def __repr__(self) -> str:  # pragma: no cover - defensive redaction
        return (
            "WebConfig(discord_client_id="
            f"{self.discord_client_id!r}, discord_client_secret='***redacted***', "
            "discord_bot_token='***redacted***', "
            f"public_base_url={self.public_base_url!r}, "
            "internal_api_token='***redacted***', "
            f"database_url={self.database_url!r}, "
            f"web_host={self.web_host!r}, web_port={self.web_port!r}, "
            f"bot_internal_base_url={self.bot_internal_base_url!r}, "
            f"log_level={self.log_level!r}, log_human={self.log_human!r}, "
            f"cookie_secure={self.cookie_secure!r}, session_ttl_days={self.session_ttl_days!r}, "
            "guild_permissions_cache_ttl_seconds="
            f"{self.guild_permissions_cache_ttl_seconds!r})"
        )


def load_web_config() -> WebConfig:
    """Load and validate web dashboard configuration.

    Raises:
        ConfigError: if required configuration is missing or malformed.
    """
    client_id_raw = os.environ.get("DISCORD_APPLICATION_ID", "").strip()
    if not client_id_raw:
        raise ConfigError(
            "DISCORD_APPLICATION_ID is not set. It doubles as the OAuth2 client ID."
        )

    client_secret = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
    if not client_secret:
        raise ConfigError(
            "DISCORD_CLIENT_SECRET is not set. Get it from the Discord Developer Portal's "
            "OAuth2 page."
        )

    bot_token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not bot_token:
        raise ConfigError("DISCORD_TOKEN is not set.")

    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        raise ConfigError(
            "PUBLIC_BASE_URL is not set. It must be the exact public URL the dashboard is "
            "reachable at (e.g. https://c3p0.example.com), matching the redirect URI "
            "registered in the Discord Developer Portal."
        )

    internal_api_token = os.environ.get("INTERNAL_API_TOKEN", "").strip()
    if not internal_api_token:
        raise ConfigError(
            "INTERNAL_API_TOKEN is not set. It must match the value given to the c3p0 (bot) "
            "service - see its own INTERNAL_API_TOKEN error for how to generate one."
        )

    try:
        return WebConfig(
            discord_client_id=int(client_id_raw),
            discord_client_secret=client_secret,
            discord_bot_token=bot_token,
            public_base_url=public_base_url,
            internal_api_token=internal_api_token,
            database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:////data/c3p0.db"),
            web_host=os.environ.get("WEB_HOST", "0.0.0.0"),  # noqa: S104
            web_port=get_int("WEB_PORT", 8080),
            bot_internal_base_url=os.environ.get("BOT_INTERNAL_BASE_URL", "http://c3p0:8100"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            log_human=get_bool("LOG_HUMAN", False),
            cookie_secure=get_bool("WEB_COOKIE_SECURE", True),
        )
    except EnvError as exc:
        raise ConfigError(str(exc)) from exc
