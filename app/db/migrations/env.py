import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make `app` importable when Alembic is invoked from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would permanently set
    # .disabled = True on every logger that already exists at this point
    # (e.g. "app.bot", every "app.cogs.*" logger) since none of them are
    # listed in alembic.ini's [loggers]. That silently no-ops every future
    # call on those loggers for the rest of the process - not just during
    # this migration - when Alembic is invoked embedded in the app (as
    # opposed to standalone via the `alembic` CLI).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # Alembic can run before the full app Config is needed (e.g. in CI
    # without a Discord token set), so read DATABASE_URL directly rather
    # than importing app.config, which requires DISCORD_TOKEN.
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:////data/c3p0.db")


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
