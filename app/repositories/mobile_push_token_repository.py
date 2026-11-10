"""Persistence for the mobile apps' push tokens (ADR 0011)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

import aiosqlite


@dataclass(frozen=True, slots=True)
class MobilePushToken:
    user_id: int
    email: str
    platform: str
    token: str = field(repr=False)
    key: str = field(repr=False)


class MobilePushTokenRepository(Protocol):
    async def upsert(
        self, user_id: int, session_jti: str, platform: str, token: str, key: str
    ) -> None: ...

    async def delete(self, user_id: int, token: str) -> bool: ...

    async def delete_for_session(self, session_jti: str) -> list[str]: ...

    async def delete_tokens(self, tokens: Iterable[str]) -> None: ...

    async def list_for_users(self, user_ids: list[int]) -> list[MobilePushToken]: ...


class SQLiteMobilePushTokenRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(
        self, user_id: int, session_jti: str, platform: str, token: str, key: str
    ) -> None:
        # A phone registering again (or another account signing in on it)
        # takes the token over: one token notifies one user only.
        await self._conn.execute(
            """
            INSERT INTO mobile_push_tokens (user_id, session_jti, platform, token, key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (token)
            DO UPDATE SET user_id = excluded.user_id,
                          session_jti = excluded.session_jti,
                          platform = excluded.platform,
                          key = excluded.key
            """,
            (user_id, session_jti, platform, token, key),
        )
        await self._conn.commit()

    async def delete(self, user_id: int, token: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM mobile_push_tokens WHERE user_id = ? AND token = ?",
            (user_id, token),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_for_session(self, session_jti: str) -> list[str]:
        """Drop the tokens this session registered; return them."""
        cursor = await self._conn.execute(
            "DELETE FROM mobile_push_tokens WHERE session_jti = ? RETURNING token",
            (session_jti,),
        )
        rows = await cursor.fetchall()
        await self._conn.commit()
        return [str(row["token"]) for row in rows]

    async def delete_tokens(self, tokens: Iterable[str]) -> None:
        await self._conn.executemany(
            "DELETE FROM mobile_push_tokens WHERE token = ?",
            [(token,) for token in tokens],
        )
        await self._conn.commit()

    async def list_for_users(self, user_ids: list[int]) -> list[MobilePushToken]:
        if not user_ids:
            return []
        placeholders = ",".join("?" * len(user_ids))
        cursor = await self._conn.execute(
            f"""
            SELECT t.user_id, u.email, t.platform, t.token, t.key
            FROM mobile_push_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.user_id IN ({placeholders})
            ORDER BY t.id
            """,
            user_ids,
        )
        rows = await cursor.fetchall()
        return [
            MobilePushToken(
                user_id=int(row["user_id"]),
                email=str(row["email"]),
                platform=str(row["platform"]),
                token=str(row["token"]),
                key=str(row["key"]),
            )
            for row in rows
        ]
