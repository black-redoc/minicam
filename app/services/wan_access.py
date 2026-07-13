"""WAN streaming/push gate: deny-by-default, no row means WAN is disabled.

Deliberately a single boolean, not a billing table — see
app/db/migrations/0001_initial.sql for why plans/subscriptions live in the
central D1 database instead of here.
"""

from __future__ import annotations

from app.repositories.wan_access_repository import WanAccessRepository


class WanAccessGateService:
    def __init__(self, wan_access_repository: WanAccessRepository) -> None:
        self._wan_access_repository = wan_access_repository

    async def is_active(self, user_id: int) -> bool:
        wan_access = await self._wan_access_repository.get(user_id)
        return wan_access is not None and wan_access.is_active
