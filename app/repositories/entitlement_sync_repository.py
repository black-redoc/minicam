"""Persistence for central entitlement syncs (minicam-admin ADR 0007).

A sync rewrites the wan_access flags of every local user from the
dashboard's answer and records when it happened, so the WAN gate can fail
closed once that answer is too old.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Protocol

import aiosqlite


class EntitlementSyncRepository(Protocol):
    async def last_synced_at(self) -> str | None: ...

    async def apply(self, allowed_emails: Iterable[str], synced_at: str) -> None: ...


class SQLiteEntitlementSyncRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def last_synced_at(self) -> str | None:
        cursor = await self._conn.execute(
            "SELECT synced_at FROM entitlement_sync WHERE id = 1"
        )
        row = await cursor.fetchone()
        return row["synced_at"] if row is not None else None

    async def apply(self, allowed_emails: Iterable[str], synced_at: str) -> None:
        """Set wan_access for every user (active only if their email, compared
        case-insensitively, is in allowed_emails), then record synced_at.

        Flags are only rewritten when they change, to keep SD card writes low.
        The sync time is written last, so a failure part-way leaves the old
        time and the gate keeps counting toward its fail-closed deadline.
        """
        allowed = json.dumps(sorted({email.lower() for email in allowed_emails}))
        await self._conn.execute(
            """
            INSERT INTO wan_access (user_id, is_active)
            SELECT u.id, lower(u.email) IN (SELECT value FROM json_each(?))
            FROM users u WHERE true
            ON CONFLICT (user_id)
            DO UPDATE SET is_active = excluded.is_active,
                          updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE wan_access.is_active <> excluded.is_active
            """,
            (allowed,),
        )
        await self._conn.execute(
            """
            INSERT INTO entitlement_sync (id, synced_at) VALUES (1, ?)
            ON CONFLICT (id) DO UPDATE SET synced_at = excluded.synced_at
            """,
            (synced_at,),
        )
        await self._conn.commit()
