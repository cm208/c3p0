from __future__ import annotations

from pathlib import Path

import pytest

from app.db import database


@pytest.fixture
async def _dispose_after() -> None:
    yield
    await database.dispose_engine()


async def test_init_engine_enables_wal_and_foreign_keys(
    tmp_path: Path, _dispose_after: None
) -> None:
    db_path = tmp_path / "pragma-check.db"
    engine = database.init_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")

    async with engine.connect() as conn:
        journal_mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        foreign_keys = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()

    assert journal_mode == "wal"
    assert foreign_keys == 1


async def test_init_engine_skips_pragmas_for_in_memory_db(_dispose_after: None) -> None:
    # :memory: databases reject WAL outright - this just confirms init_engine
    # doesn't try to set it and blow up.
    engine = database.init_engine("sqlite+aiosqlite:///:memory:")

    async with engine.connect() as conn:
        journal_mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()

    assert journal_mode == "memory"
