"""Tests for app.db.connection: migration application and connection lifecycle."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

import app.db.connection as connection_module
from app.db.connection import (
    apply_migrations,
    close_db,
    get_connection,
    init_db,
    split_sql_statements,
)


async def test_apply_migrations_creates_expected_tables(
    db_connection: aiosqlite.Connection,
) -> None:
    cursor = await db_connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cursor.fetchall()
    table_names = {row["name"] for row in rows}
    assert {
        "users",
        "sessions",
        "camera_permissions",
        "wan_access",
        "web_push_subscriptions",
        "vapid_keys",
    } <= table_names


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


async def _versions(conn: aiosqlite.Connection) -> list[str]:
    cursor = await conn.execute("SELECT version FROM schema_migrations ORDER BY 1")
    return [row[0] for row in await cursor.fetchall()]


async def _tables(conn: aiosqlite.Connection) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in await cursor.fetchall()}


@pytest.fixture()
def migrations_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "migrations"
    path.mkdir()
    monkeypatch.setattr(connection_module, "_MIGRATIONS_DIR", path)
    return path


async def _fresh() -> aiosqlite.Connection:
    return await aiosqlite.connect(":memory:")


class TestVersionedMigrations:
    async def test_records_each_applied_version(
        self, db_connection: aiosqlite.Connection
    ) -> None:
        assert await _versions(db_connection) == ["0001_initial", "0002_web_push"]

    async def test_applies_pending_migrations_in_name_order(
        self, migrations_dir: Path
    ) -> None:
        (migrations_dir / "0002_b.sql").write_text("ALTER TABLE a ADD COLUMN y;\n")
        (migrations_dir / "0001_a.sql").write_text("CREATE TABLE a (x);\n")
        conn = await _fresh()
        try:
            await apply_migrations(conn)
            cursor = await conn.execute("SELECT x, y FROM a")
            assert await cursor.fetchall() == []
            assert await _versions(conn) == ["0001_a", "0002_b"]
        finally:
            await conn.close()

    async def test_already_applied_migration_is_not_run_again(
        self, migrations_dir: Path
    ) -> None:
        # Non-idempotent on purpose: a second run would fail if re-executed.
        (migrations_dir / "0001_a.sql").write_text(
            "CREATE TABLE a (x);\nINSERT INTO a VALUES (1);\n"
        )
        conn = await _fresh()
        try:
            await apply_migrations(conn)
            await apply_migrations(conn)
            cursor = await conn.execute("SELECT count(*) FROM a")
            assert (await cursor.fetchone())[0] == 1
        finally:
            await conn.close()

    async def test_failing_migration_is_rolled_back_entirely(
        self, migrations_dir: Path
    ) -> None:
        (migrations_dir / "0001_a.sql").write_text("CREATE TABLE a (x);\n")
        (migrations_dir / "0002_bad.sql").write_text(
            "CREATE TABLE b (x);\nINSERT INTO missing VALUES (1);\n"
        )
        conn = await _fresh()
        try:
            with pytest.raises(sqlite3.OperationalError, match="missing"):
                await apply_migrations(conn)
            assert "b" not in await _tables(conn)
            assert await _versions(conn) == ["0001_a"]
        finally:
            await conn.close()

    async def test_upgrades_a_database_created_before_versioning(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "legacy.db"
        legacy = await aiosqlite.connect(db_path)
        sql = (connection_module._MIGRATIONS_DIR / "0001_initial.sql").read_text()
        await legacy.executescript(sql)
        await legacy.execute(
            "INSERT INTO users (email, password_hash) VALUES ('a@b.c', 'h')"
        )
        await legacy.commit()
        await legacy.close()

        conn = await aiosqlite.connect(db_path)
        try:
            await apply_migrations(conn)
            assert await _versions(conn) == ["0001_initial", "0002_web_push"]
            cursor = await conn.execute("SELECT email FROM users")
            assert [row[0] for row in await cursor.fetchall()] == ["a@b.c"]
        finally:
            await conn.close()

    async def test_concurrent_runners_apply_each_migration_once(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        # Like the CLI and the server starting together on one file.
        (migrations_dir / "0001_a.sql").write_text(
            "CREATE TABLE a (x);\nINSERT INTO a VALUES (1);\n"
        )
        db_path = tmp_path / "shared.db"
        first = await aiosqlite.connect(db_path)
        second = await aiosqlite.connect(db_path)
        try:
            await asyncio.gather(apply_migrations(first), apply_migrations(second))
            cursor = await first.execute("SELECT count(*) FROM a")
            assert (await cursor.fetchone())[0] == 1
            assert await _versions(first) == ["0001_a"]
        finally:
            await first.close()
            await second.close()


class TestSplitSqlStatements:
    def test_splits_one_statement_per_terminated_line(self) -> None:
        assert split_sql_statements("CREATE TABLE a (x);\nCREATE TABLE b (y);\n") == [
            "CREATE TABLE a (x);",
            "CREATE TABLE b (y);",
        ]

    def test_keeps_multiline_statements_and_comments_together(self) -> None:
        script = "-- users\nCREATE TABLE a (\n    x INTEGER\n);\n"
        assert split_sql_statements(script) == [
            "-- users\nCREATE TABLE a (\n    x INTEGER\n);"
        ]

    def test_keeps_trigger_body_in_one_statement(self) -> None:
        script = (
            "CREATE TRIGGER t AFTER INSERT ON a BEGIN\n    UPDATE a SET x = 1;\nEND;\n"
        )
        assert len(split_sql_statements(script)) == 1

    def test_ignores_trailing_comments(self) -> None:
        assert split_sql_statements("CREATE TABLE a (x);\n-- done\n") == [
            "CREATE TABLE a (x);"
        ]

    def test_rejects_unterminated_statement(self) -> None:
        with pytest.raises(ValueError, match="Unterminated"):
            split_sql_statements("CREATE TABLE a (x)\n")
