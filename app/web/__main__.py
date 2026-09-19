"""Web dashboard entrypoint.

Run with:  python -m app.web
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from app import __version__
from app.config import ConfigError
from app.db.database import dispose_engine, init_engine
from app.logging import configure_logging, get_logger
from app.web.app import create_app
from app.web.config import load_web_config

logger = get_logger(__name__)


async def main() -> None:
    try:
        config = load_web_config()
    except ConfigError as exc:
        # Logging isn't configured yet at this point (it depends on config),
        # so this goes straight to stderr.
        print(f"Configuration error: {exc}")  # noqa: T201
        raise SystemExit(1) from exc

    configure_logging(level=config.log_level, human=config.log_human)
    logger.info("Starting C3P0 web dashboard", extra={"version": __version__})

    # Migrations only ever run from the bot process (app/__main__.py) -
    # running Alembic from two processes against the same fresh SQLite file
    # would race. compose.yaml's `depends_on: c3p0: condition: service_healthy`
    # guarantees the bot has already migrated before this container starts.
    init_engine(config.database_url)

    app = create_app(config)
    # log_config=None stops uvicorn from calling logging.config.dictConfig()
    # on startup, which would otherwise reconfigure the *root* logger and
    # silently swallow every app-level logger.info() call - the exact same
    # class of bug Alembic's own fileConfig() call caused for the bot
    # process (see app/__main__.py's comment on this).
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.web_host, port=config.web_port, log_config=None)
    )

    try:
        await server.serve()
    finally:
        await dispose_engine()
        logger.info("C3P0 web dashboard stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted")
