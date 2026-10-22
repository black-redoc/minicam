"""Persistence for the camera_permissions table (deny-by-default authorization)."""

from __future__ import annotations

from typing import Protocol

import aiosqlite

from app.models.domain import CameraPermission


class PermissionRepository(Protocol):
    async def get(self, user_id: int, camera_name: str) -> CameraPermission | None: ...

    async def list_for_user(self, user_id: int) -> list[CameraPermission]: ...

    async def upsert(
        self, user_id: int, camera_name: str, can_view: bool, can_use_ptz: bool
    ) -> CameraPermission: ...

    async def list_viewer_ids(self, camera_name: str) -> list[int]: ...


def _row_to_permission(row: aiosqlite.Row) -> CameraPermission:
    return CameraPermission(
        id=row["id"],
        user_id=row["user_id"],
        camera_name=row["camera_name"],
        can_view=bool(row["can_view"]),
        can_use_ptz=bool(row["can_use_ptz"]),
        created_at=row["created_at"],
    )


class SQLitePermissionRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, user_id: int, camera_name: str) -> CameraPermission | None:
        cursor = await self._conn.execute(
            "SELECT * FROM camera_permissions WHERE user_id = ? AND camera_name = ?",
            (user_id, camera_name),
        )
        row = await cursor.fetchone()
        return _row_to_permission(row) if row is not None else None

    async def list_for_user(self, user_id: int) -> list[CameraPermission]:
        cursor = await self._conn.execute(
            "SELECT * FROM camera_permissions WHERE user_id = ?", (user_id,)
        )
        rows = await cursor.fetchall()
        return [_row_to_permission(row) for row in rows]

    async def upsert(
        self, user_id: int, camera_name: str, can_view: bool, can_use_ptz: bool
    ) -> CameraPermission:
        cursor = await self._conn.execute(
            """
            INSERT INTO camera_permissions (user_id, camera_name, can_view, can_use_ptz)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, camera_name)
            DO UPDATE SET can_view = excluded.can_view,
                          can_use_ptz = excluded.can_use_ptz
            RETURNING *
            """,
            (user_id, camera_name, int(can_view), int(can_use_ptz)),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return _row_to_permission(row)

    async def list_viewer_ids(self, camera_name: str) -> list[int]:
        """Return the ids of active users allowed to view camera_name."""
        cursor = await self._conn.execute(
            """
            SELECT p.user_id FROM camera_permissions p
            JOIN users u ON u.id = p.user_id
            WHERE p.camera_name = ? AND p.can_view = 1 AND u.is_active = 1
            ORDER BY p.user_id
            """,
            (camera_name,),
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]
