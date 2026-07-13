"""Persistence for the sessions table (revocable session JWTs)."""

from __future__ import annotations

from typing import Protocol

import aiosqlite

from app.models.domain import Session


class SessionRepository(Protocol):
    async def create(
        self,
        jti: str,
        user_id: int,
        expires_at: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> Session: ...

    async def get(self, jti: str) -> Session | None: ...

    async def revoke(self, jti: str) -> None: ...


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        jti=row["jti"],
        user_id=row["user_id"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        user_agent=row["user_agent"],
        ip_address=row["ip_address"],
    )


class SQLiteSessionRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(
        self,
        jti: str,
        user_id: int,
        expires_at: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> Session:
        cursor = await self._conn.execute(
            """
            INSERT INTO sessions (jti, user_id, expires_at, user_agent, ip_address)
            VALUES (?, ?, ?, ?, ?)
            RETURNING *
            """,
            (jti, user_id, expires_at, user_agent, ip_address),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return _row_to_session(row)

    async def get(self, jti: str) -> Session | None:
        cursor = await self._conn.execute(
            "SELECT * FROM sessions WHERE jti = ?", (jti,)
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row is not None else None

    async def revoke(self, jti: str) -> None:
        await self._conn.execute(
            """
            UPDATE sessions
            SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE jti = ?
            """,
            (jti,),
        )
        await self._conn.commit()
