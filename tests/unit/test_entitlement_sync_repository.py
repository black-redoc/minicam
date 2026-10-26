"""Tests for SQLiteEntitlementSyncRepository (minicam-admin specs/011 AC7)."""

from __future__ import annotations

import aiosqlite

from app.repositories.entitlement_sync_repository import (
    SQLiteEntitlementSyncRepository,
)
from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository


async def _flags(conn: aiosqlite.Connection) -> dict[str, bool]:
    cursor = await conn.execute(
        "SELECT u.email, w.is_active FROM users u "
        "JOIN wan_access w ON w.user_id = u.id ORDER BY u.email"
    )
    return {row["email"]: bool(row["is_active"]) for row in await cursor.fetchall()}


async def test_sync_applies_entitlements(db_connection: aiosqlite.Connection) -> None:
    users = SQLiteUserRepository(db_connection)
    ana = await users.create("Ana@Example.com", "h", is_admin=False)
    await users.create("beto@example.com", "h", is_admin=False)
    caro = await users.create("caro@example.com", "h", is_admin=False)
    await users.create("admin@example.com", "h", is_admin=True)
    wan = SQLiteWanAccessRepository(db_connection)
    await wan.set_active(caro.id, True)  # granted by hand before central mode
    repo = SQLiteEntitlementSyncRepository(db_connection)
    assert await repo.last_synced_at() is None

    await repo.apply(
        {"ana@example.com", "nobody@example.com"}, "2026-10-26T12:00:00.000Z"
    )

    # Emails match case-insensitively; everyone not allowed is inactive.
    assert await _flags(db_connection) == {
        "Ana@Example.com": True,
        "admin@example.com": False,
        "beto@example.com": False,
        "caro@example.com": False,
    }
    assert await repo.last_synced_at() == "2026-10-26T12:00:00.000Z"

    # An unchanged flag is not rewritten; the sync time always is.
    before = await wan.get(ana.id)
    await repo.apply({"ANA@example.com"}, "2026-10-26T12:01:00.000Z")
    assert await wan.get(ana.id) == before
    assert await repo.last_synced_at() == "2026-10-26T12:01:00.000Z"

    await repo.apply(set(), "2026-10-26T12:02:00.000Z")
    assert not any((await _flags(db_connection)).values())


async def test_only_one_sync_row(db_connection: aiosqlite.Connection) -> None:
    repo = SQLiteEntitlementSyncRepository(db_connection)
    await repo.apply(set(), "2026-10-26T12:00:00.000Z")
    await repo.apply(set(), "2026-10-26T12:01:00.000Z")
    cursor = await db_connection.execute("SELECT count(*) AS n FROM entitlement_sync")
    row = await cursor.fetchone()
    assert row is not None and row["n"] == 1
