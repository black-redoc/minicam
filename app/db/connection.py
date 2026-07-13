"""aiosqlite connection lifecycle: migration application and a process-wide
connection used by the FastAPI dependency graph.

A single shared connection is intentional, not a shortcut: aiosqlite serializes
access to it on its own background thread, and SQLite itself only supports one
writer at a time, so a connection pool would add complexity without adding
concurrency. Tests never touch this singleton — they build their own
short-lived `:memory:` connection (see tests/unit/conftest.py) and inject it
directly into repositories.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_connection: aiosqlite.Connection | None = None


async def apply_migrations(conn: aiosqlite.Connection) -> None:
    """Run every .sql file in db/migrations, in filename order.

    Safe to call against a database that already has the schema applied:
    every statement in the migrations uses IF NOT EXISTS, so re-running is
    a no-op. This matters because both init_db() (server startup) and the
    CLI (app/cli/create_user.py) call this independently against the same
    file — without idempotent DDL, running the CLI once and then starting
    the server would fail with "table already exists".
    """
    for migration_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        script = migration_path.read_text(encoding="utf-8")
        await conn.executescript(script)
    await conn.commit()


async def init_db(db_path: str | Path) -> aiosqlite.Connection:
    """Open the process-wide connection and apply migrations.

    Safe to call once at application startup (see main.py lifespan).
    """
    global _connection
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await apply_migrations(conn)
    _connection = conn
    return conn


async def close_db() -> None:
    """Close the process-wide connection, if any."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None


def get_connection() -> aiosqlite.Connection:
    """Return the process-wide connection.

    Raises:
        RuntimeError: if init_db() has not run yet.
    """
    if _connection is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _connection
