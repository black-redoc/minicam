"""Persistence for the wan_access table (a single flag, no billing data)."""

from __future__ import annotations

from typing import Protocol

import aiosqlite

from app.models.domain import WanAccess


class WanAccessRepository(Protocol):
    async def get(self, user_id: int) -> WanAccess | None: ...

    async def set_active(self, user_id: int, is_active: bool) -> WanAccess: ...


def _row_to_wan_access(row: aiosqlite.Row) -> WanAccess:
    return WanAccess(
        user_id=row["user_id"],
        is_active=bool(row["is_active"]),
        updated_at=row["updated_at"],
    )


class SQLiteWanAccessRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, user_id: int) -> WanAccess | None:
        cursor = await self._conn.execute(
            "SELECT * FROM wan_access WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return _row_to_wan_access(row) if row is not None else None

    async def set_active(self, user_id: int, is_active: bool) -> WanAccess:
        cursor = await self._conn.execute(
            """
            INSERT INTO wan_access (user_id, is_active)
            VALUES (?, ?)
            ON CONFLICT (user_id)
            DO UPDATE SET is_active = excluded.is_active,
                          updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            RETURNING *
            """,
            (user_id, int(is_active)),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return _row_to_wan_access(row)
