"""Persistence for event recording metadata (the MP4 files live on disk)."""

from __future__ import annotations

import json
from typing import Protocol

import aiosqlite

from app.models.domain import Recording


class RecordingRepository(Protocol):
    async def add(
        self,
        camera_name: str,
        file_name: str,
        started_at: str,
        duration_seconds: float,
        size_bytes: int,
        classes: tuple[str, ...],
    ) -> Recording: ...

    async def get(self, recording_id: int) -> Recording | None: ...

    async def list_for_cameras(
        self, camera_names: list[str], before_id: int | None, limit: int
    ) -> list[Recording]: ...

    async def oldest(self) -> Recording | None: ...

    async def total_size_bytes(self) -> int: ...

    async def list_file_names(self) -> set[str]: ...

    async def delete(self, recording_id: int) -> bool: ...

    async def delete_by_file_name(self, file_name: str) -> bool: ...


def _row_to_recording(row: aiosqlite.Row) -> Recording:
    return Recording(
        id=row["id"],
        camera_name=row["camera_name"],
        file_name=row["file_name"],
        started_at=row["started_at"],
        duration_seconds=row["duration_seconds"],
        size_bytes=row["size_bytes"],
        classes=tuple(json.loads(row["classes"])),
        created_at=row["created_at"],
    )


class SQLiteRecordingRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(
        self,
        camera_name: str,
        file_name: str,
        started_at: str,
        duration_seconds: float,
        size_bytes: int,
        classes: tuple[str, ...],
    ) -> Recording:
        cursor = await self._conn.execute(
            """
            INSERT INTO recordings
                (camera_name, file_name, started_at, duration_seconds,
                 size_bytes, classes)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                camera_name,
                file_name,
                started_at,
                duration_seconds,
                size_bytes,
                json.dumps(list(classes)),
            ),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return _row_to_recording(row)

    async def get(self, recording_id: int) -> Recording | None:
        cursor = await self._conn.execute(
            "SELECT * FROM recordings WHERE id = ?", (recording_id,)
        )
        row = await cursor.fetchone()
        return _row_to_recording(row) if row is not None else None

    async def list_for_cameras(
        self, camera_names: list[str], before_id: int | None, limit: int
    ) -> list[Recording]:
        """Newest first. Pass the last id of a page as before_id for the next."""
        if not camera_names:
            return []
        placeholders = ",".join("?" * len(camera_names))
        params: list[object] = list(camera_names)
        before_clause = ""
        if before_id is not None:
            before_clause = "AND id < ?"
            params.append(before_id)
        params.append(limit)
        # Only "?" placeholders are interpolated into the SQL text.
        cursor = await self._conn.execute(
            f"""
            SELECT * FROM recordings
            WHERE camera_name IN ({placeholders}) {before_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        )
        return [_row_to_recording(row) for row in await cursor.fetchall()]

    async def oldest(self) -> Recording | None:
        cursor = await self._conn.execute(
            "SELECT * FROM recordings ORDER BY id ASC LIMIT 1"
        )
        row = await cursor.fetchone()
        return _row_to_recording(row) if row is not None else None

    async def total_size_bytes(self) -> int:
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM recordings"
        )
        row = await cursor.fetchone()
        assert row is not None
        return int(row["total"])

    async def list_file_names(self) -> set[str]:
        cursor = await self._conn.execute("SELECT file_name FROM recordings")
        return {row["file_name"] for row in await cursor.fetchall()}

    async def delete(self, recording_id: int) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM recordings WHERE id = ?", (recording_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_by_file_name(self, file_name: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM recordings WHERE file_name = ?", (file_name,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0
