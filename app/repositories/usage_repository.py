"""Persistence for WAN usage counters (minicam-admin ADR 0008, minicam ADR 0004).

Counters are cumulative per (Colombia month, email, camera). A flush adds the
bytes counted in memory; a report reads the counters that grew past the last
value the dashboard accepted and, once it answers 200, records that value.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

import aiosqlite


@dataclass(frozen=True, slots=True)
class UsageCounter:
    month: str
    email: str
    camera: str
    wan_bytes: int


class UsageRepository(Protocol):
    async def add(self, month: str, pending: Mapping[tuple[str, str], int]) -> None: ...

    async def reporter_id(self) -> str: ...

    async def unacked(self, months: Collection[str]) -> list[UsageCounter]: ...

    async def acknowledge(self, counters: Iterable[UsageCounter]) -> None: ...


class SQLiteUsageRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, month: str, pending: Mapping[tuple[str, str], int]) -> None:
        """Add pending bytes, keyed by (email, camera), to month's counters in
        one transaction: either every counter grows or none does."""
        try:
            await self._conn.executemany(
                """
                INSERT INTO usage_counters (month, email, camera, wan_bytes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (month, email, camera)
                DO UPDATE SET wan_bytes = wan_bytes + excluded.wan_bytes
                """,
                [
                    (month, email, camera, nbytes)
                    for (email, camera), nbytes in pending.items()
                ],
            )
            await self._conn.commit()
        except BaseException:
            await self._conn.rollback()
            raise

    async def reporter_id(self) -> str:
        """This install's reporter ID, created the first time it is asked for."""
        await self._conn.execute(
            "INSERT INTO usage_reporter (id, reporter_id) VALUES (1, ?) "
            "ON CONFLICT (id) DO NOTHING",
            (str(uuid.uuid4()),),
        )
        await self._conn.commit()
        cursor = await self._conn.execute(
            "SELECT reporter_id FROM usage_reporter WHERE id = 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        return str(row["reporter_id"])

    async def unacked(self, months: Collection[str]) -> list[UsageCounter]:
        """Counters of the given months that grew since the last accepted report."""
        placeholders = ", ".join("?" for _ in months)
        cursor = await self._conn.execute(
            "SELECT month, email, camera, wan_bytes FROM usage_counters "
            f"WHERE wan_bytes > acked_bytes AND month IN ({placeholders}) "
            "ORDER BY month, email, camera",
            tuple(months),
        )
        return [
            UsageCounter(row["month"], row["email"], row["camera"], row["wan_bytes"])
            for row in await cursor.fetchall()
        ]

    async def acknowledge(self, counters: Iterable[UsageCounter]) -> None:
        """Record the values the dashboard accepted (never lowering one)."""
        await self._conn.executemany(
            "UPDATE usage_counters SET acked_bytes = MAX(acked_bytes, ?) "
            "WHERE month = ? AND email = ? AND camera = ?",
            [(c.wan_bytes, c.month, c.email, c.camera) for c in counters],
        )
        await self._conn.commit()
