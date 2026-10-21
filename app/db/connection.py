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

import logging
import sqlite3
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

_connection: aiosqlite.Connection | None = None


def split_sql_statements(script: str) -> list[str]:
    """Split a migration script into single statements.

    Uses sqlite3.complete_statement(), so trigger bodies (BEGIN ... END;)
    stay in one piece. Expects each statement to end its own line.

    Raises:
        ValueError: if the script ends with an unterminated statement.
    """
    statements: list[str] = []
    current = ""
    for line in script.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            statements.append(current.strip())
            current = ""
    leftover = [
        line
        for line in current.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    if leftover:
        raise ValueError(f"Unterminated SQL statement: {current.strip()[:80]!r}")
    return statements


async def apply_migrations(conn: aiosqlite.Connection) -> None:
    """Apply every db/migrations/*.sql file not yet recorded, in name order.

    Each migration runs in its own BEGIN IMMEDIATE transaction together with
    the schema_migrations row that records it, so it is applied exactly
    once and either fully or not at all — even when the CLI
    (app/cli/create_user.py) and the server start against the same file at
    the same time: the second one waits for the write lock, then sees the
    version already recorded. Migrations may therefore use plain ALTER
    TABLE; they no longer need to be idempotent. A database created before
    versioning existed simply records 0001 on first run (its DDL is
    IF NOT EXISTS, so re-running it is a no-op).
    """
    await conn.execute(_SCHEMA_MIGRATIONS_DDL)
    await conn.commit()
    for migration_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = migration_path.stem
        statements = split_sql_statements(migration_path.read_text(encoding="utf-8"))
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            )
            if await cursor.fetchone() is None:
                for statement in statements:
                    await conn.execute(statement)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
                )
                logger.info("Applied database migration %s", version)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise


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
