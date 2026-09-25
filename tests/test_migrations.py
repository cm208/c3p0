from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.__main__ import run_migrations


@pytest.fixture
def _sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    # env.py's online migration path reads DATABASE_URL from the process
    # environment directly (see app/db/migrations/env.py:_database_url),
    # rather than from the Alembic Config object run_migrations() builds -
    # so the env var, not the function argument, is what actually selects
    # the database being migrated.
    db_path = tmp_path / "migrations.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def test_run_migrations_applies_schema(_sqlite_url: str, tmp_path: Path) -> None:
    run_migrations(_sqlite_url)

    assert (tmp_path / "migrations.db").exists()


def test_run_migrations_does_not_disable_other_loggers(_sqlite_url: str) -> None:
    """Regression test.

    Alembic's env.py calls logging.config.fileConfig(alembic.ini), which
    defaults to disable_existing_loggers=True. That would silently set
    .disabled = True on every logger that already exists and isn't listed in
    alembic.ini's [loggers] (e.g. "app.bot"), permanently no-op'ing it for
    the rest of the process - not just during the migration.
    """
    unrelated_logger = logging.getLogger("app.test_migrations_sentinel")
    unrelated_logger.disabled = False

    run_migrations(_sqlite_url)

    assert unrelated_logger.disabled is False


def test_migration_0006_adds_event_table_and_use_count(_sqlite_url: str, tmp_path: Path) -> None:
    import sqlite3

    run_migrations(_sqlite_url)

    with sqlite3.connect(tmp_path / "migrations.db") as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        command_columns = {row[1] for row in conn.execute("PRAGMA table_info(custom_command)")}

    assert "guild_event" in tables
    assert "use_count" in command_columns
