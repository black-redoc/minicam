"""Persistence for Web Push subscriptions and the device's VAPID key."""

from __future__ import annotations

from typing import Protocol

import aiosqlite

from app.models.domain import PushSubscription


class PushSubscriptionRepository(Protocol):
    async def upsert(
        self, user_id: int, endpoint: str, p256dh: str, auth: str
    ) -> PushSubscription: ...

    async def delete(self, user_id: int, endpoint: str) -> bool: ...

    async def delete_by_endpoint(self, endpoint: str) -> None: ...

    async def list_for_users(self, user_ids: list[int]) -> list[PushSubscription]: ...

    async def get_vapid_private_key(self) -> str | None: ...

    async def save_vapid_private_key(self, private_key_pem: str) -> str: ...


def _row_to_subscription(row: aiosqlite.Row) -> PushSubscription:
    return PushSubscription(
        id=row["id"],
        user_id=row["user_id"],
        endpoint=row["endpoint"],
        p256dh=row["p256dh"],
        auth=row["auth"],
        created_at=row["created_at"],
    )


class SQLitePushSubscriptionRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(
        self, user_id: int, endpoint: str, p256dh: str, auth: str
    ) -> PushSubscription:
        # The same browser re-subscribing (or another account logging in on
        # it) takes the endpoint over: one endpoint notifies one user only.
        cursor = await self._conn.execute(
            """
            INSERT INTO web_push_subscriptions (user_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (endpoint)
            DO UPDATE SET user_id = excluded.user_id,
                          p256dh = excluded.p256dh,
                          auth = excluded.auth
            RETURNING *
            """,
            (user_id, endpoint, p256dh, auth),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return _row_to_subscription(row)

    async def delete(self, user_id: int, endpoint: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM web_push_subscriptions WHERE user_id = ? AND endpoint = ?",
            (user_id, endpoint),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_by_endpoint(self, endpoint: str) -> None:
        await self._conn.execute(
            "DELETE FROM web_push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        await self._conn.commit()

    async def list_for_users(self, user_ids: list[int]) -> list[PushSubscription]:
        if not user_ids:
            return []
        placeholders = ", ".join("?" for _ in user_ids)
        cursor = await self._conn.execute(
            "SELECT * FROM web_push_subscriptions "
            f"WHERE user_id IN ({placeholders}) ORDER BY id",
            tuple(user_ids),
        )
        rows = await cursor.fetchall()
        return [_row_to_subscription(row) for row in rows]

    async def get_vapid_private_key(self) -> str | None:
        cursor = await self._conn.execute(
            "SELECT private_key_pem FROM vapid_keys WHERE id = 1"
        )
        row = await cursor.fetchone()
        return str(row["private_key_pem"]) if row is not None else None

    async def save_vapid_private_key(self, private_key_pem: str) -> str:
        """Store the key unless one already exists; return the stored key.

        INSERT OR IGNORE makes a concurrent first start (server + CLI) keep
        whichever key won, instead of the loser overwriting it.
        """
        await self._conn.execute(
            "INSERT OR IGNORE INTO vapid_keys (id, private_key_pem) VALUES (1, ?)",
            (private_key_pem,),
        )
        await self._conn.commit()
        stored = await self.get_vapid_private_key()
        assert stored is not None
        return stored
