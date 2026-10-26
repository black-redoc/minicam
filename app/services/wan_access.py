"""WAN streaming/push gate: deny-by-default, no row means WAN is disabled.

Deliberately a single boolean, not a billing table — see
app/db/migrations/0001_initial.sql for why plans/subscriptions live in the
central D1 database instead of here.

In central mode (minicam-admin ADR 0007) the flags are a cache of the
dashboard's entitlements, trusted only while the last successful sync is at
most MAX_SYNC_AGE old: after that every WAN request is denied (fail closed).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.repositories.entitlement_sync_repository import EntitlementSyncRepository
from app.repositories.wan_access_repository import WanAccessRepository

MAX_SYNC_AGE = timedelta(minutes=30)


class WanAccessGateService:
    def __init__(
        self,
        wan_access_repository: WanAccessRepository,
        entitlement_sync_repository: EntitlementSyncRepository | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._wan_access_repository = wan_access_repository
        self._entitlement_sync_repository = entitlement_sync_repository
        self._now = now

    async def is_active(self, user_id: int) -> bool:
        wan_access = await self._wan_access_repository.get(user_id)
        if wan_access is None or not wan_access.is_active:
            return False
        if self._entitlement_sync_repository is None:
            return True
        return _is_fresh(
            await self._entitlement_sync_repository.last_synced_at(), self._now()
        )


def _is_fresh(synced_at: str | None, now: datetime) -> bool:
    """Fail closed: no sync, an unparsable time, or one in the future (the
    clock jumped back) counts as stale."""
    if synced_at is None:
        return False
    try:
        at = datetime.fromisoformat(synced_at)
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return timedelta(0) <= now - at <= MAX_SYNC_AGE
