"""Tests for app.db.connection: migration application and connection lifecycle."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.db.connection import apply_migrations, close_db, get_connection, init_db


async def test_apply_migrations_creates_expected_tables(
    db_connection: aiosqlite.Connection,
) -> None:
    cursor = await db_connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cursor.fetchall()
    table_names = {row["name"] for row in rows}
    assert {"users", "sessions", "camera_permissions", "wan_access"} <= table_names


async def test_apply_migrations_is_idempotent(
    db_connection: aiosqlite.Connection,
) -> None:
    """Regression test: the CLI (create_user.py) and the server's lifespan
    both call init_db() -> apply_migrations() independently against the
    same database file. Re-running must not raise "table already exists".
    """
    await apply_migrations(db_connection)  # must not raise


async def test_init_db_opens_and_registers_process_wide_connection() -> None:
    conn = await init_db(":memory:")
    try:
        assert get_connection() is conn
    finally:
        await close_db()


async def test_init_db_is_safe_to_call_twice_against_the_same_file(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "regression.db")
    conn = await init_db(db_path)
    await close_db()
    conn = await init_db(db_path)  # must not raise "table already exists"
    try:
        assert get_connection() is conn
    finally:
        await close_db()


async def test_close_db_clears_the_singleton() -> None:
    await init_db(":memory:")
    await close_db()
    with pytest.raises(RuntimeError, match="Database not initialized"):
        get_connection()


async def test_get_connection_raises_before_init() -> None:
    # close_db() from the previous test already reset the module singleton.
    with pytest.raises(RuntimeError, match="Database not initialized"):
        get_connection()
