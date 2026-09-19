"""Application entrypoint.

Run with:  python -m app
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from app import __version__
from app.bot import C3P0Bot
from app.config import ConfigError, load_config
from app.db.database import dispose_engine, init_engine
from app.health import mark_unhealthy
from app.logging import configure_logging, get_logger
from app.metrics import start_metrics_server

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_migrations(database_url: str) -> None:
    """Apply pending Alembic migrations synchronously before the bot starts.

    Alembic's Config object is built in code (rather than only relying on
    alembic.ini's DATABASE_URL) so this always migrates the same database
    the running process will connect to.
    """
    alembic_cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "app" / "db" / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(alembic_cfg, "head")


async def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        # Logging isn't configured yet at this point (it depends on config),
        # so this goes straight to stderr.
        print(f"Configuration error: {exc}")  # noqa: T201
        raise SystemExit(1) from exc

    configure_logging(level=config.log_level, human=config.log_human)
    logger.info("Starting C3P0", extra={"version": __version__})

    # run_migrations() is a blocking call whose Alembic env.py does its own
    # asyncio.run() - it must not be invoked directly from inside this
    # already-running loop (nested asyncio.run() raises RuntimeError), so it
    # runs in a worker thread instead.
    await asyncio.to_thread(run_migrations, config.database_url)
    # Alembic's env.py calls logging.config.fileConfig(alembic.ini) as a side
    # effect of running migrations, which reconfigures the *root* logger
    # (alembic.ini sets it to WARNING with its own plain-text handler) -
    # silently swallowing every app-level logger.info() call for the rest of
    # the process. configure_logging() is idempotent specifically so it can
    # be re-asserted here to undo that.
    configure_logging(level=config.log_level, human=config.log_human)
    init_engine(config.database_url)
    start_metrics_server(config.metrics_host, config.metrics_port, __version__)

    bot = C3P0Bot(config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.info("Received shutdown signal", extra={"signal": sig_name})
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
        except NotImplementedError:
            # add_signal_handler is Unix-only; on Windows we fall back to the
            # default KeyboardInterrupt handling around asyncio.run() below.
            pass

    async with bot:
        bot_task = asyncio.create_task(bot.start(config.discord_token))
        stop_task = asyncio.create_task(stop_event.wait())

        done, pending = await asyncio.wait(
            {bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done:
            logger.info("Shutting down gracefully")
            await bot.close()

        for task in pending:
            task.cancel()

        if bot_task in done and bot_task.exception():
            raise bot_task.exception()  # type: ignore[misc]

    mark_unhealthy()
    await dispose_engine()
    logger.info("C3P0 stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted")
