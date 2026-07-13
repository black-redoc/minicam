"""Persistence for the users table."""

from __future__ import annotations

from typing import Protocol

import aiosqlite

from app.models.domain import User


class UserRepository(Protocol):
    async def get_by_id(self, user_id: int) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def create(self, email: str, password_hash: str, is_admin: bool) -> User: ...

    async def list_all(self) -> list[User]: ...

    async def count_admins(self) -> int: ...

    async def delete(self, user_id: int) -> None: ...


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        is_admin=bool(row["is_admin"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SQLiteUserRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_by_id(self, user_id: int) -> User | None:
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return _row_to_user(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        )
        row = await cursor.fetchone()
        return _row_to_user(row) if row is not None else None

    async def create(self, email: str, password_hash: str, is_admin: bool) -> User:
        cursor = await self._conn.execute(
            """
            INSERT INTO users (email, password_hash, is_admin)
            VALUES (?, ?, ?)
            RETURNING *
            """,
            (email, password_hash, int(is_admin)),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None  # INSERT ... RETURNING always yields a row
        return _row_to_user(row)

    async def list_all(self) -> list[User]:
        cursor = await self._conn.execute("SELECT * FROM users ORDER BY email")
        rows = await cursor.fetchall()
        return [_row_to_user(row) for row in rows]

    async def count_admins(self) -> int:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1"
        )
        row = await cursor.fetchone()
        assert row is not None  # COUNT(*) always yields exactly one row
        count: int = row[0]
        return count

    async def delete(self, user_id: int) -> None:
        await self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await self._conn.commit()
