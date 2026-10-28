"""Pulls WAN entitlements from the central dashboard (minicam-admin ADR 0007).

Every SYNC_INTERVAL_SECONDS minicam calls GET /api/device/entitlements with
its device token and, on a valid answer, rewrites the local wan_access flags
from it. Any failure (network, 401, 5xx, malformed body) keeps the last
cache; the WAN gate then fails closed once it is older than MAX_SYNC_AGE
(app/services/wan_access.py). LAN access never depends on this.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeGuard

import httpx

from app.repositories.entitlement_sync_repository import EntitlementSyncRepository

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 10.0
ENTITLEMENTS_PATH = "/api/device/entitlements"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_USERS = 10_000
MAX_EMAIL_LENGTH = 254
_REASONS = frozenset({"ok", "unpaid", "manual_block"})


def parse_entitlements(data: object) -> frozenset[str] | None:
    """Lowercased emails allowed on WAN, or None if the body is not exactly
    what the dashboard sends. A user listed twice, or with a WAN flag that
    contradicts its reason, rejects the whole answer."""
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("month"), str) or not isinstance(
        data.get("generatedAt"), str
    ):
        return None
    users = data.get("users")
    if not isinstance(users, list) or len(users) > MAX_USERS:
        return None
    seen: set[str] = set()
    allowed: set[str] = set()
    for user in users:
        if not isinstance(user, dict):
            return None
        email, wan, reason = (
            user.get("minicamUser"),
            user.get("wan"),
            user.get("reason"),
        )
        if not is_email(email) or not isinstance(wan, bool) or reason not in _REASONS:
            return None
        if wan != (reason == "ok"):
            return None
        key = email.lower()
        if key in seen:
            return None
        seen.add(key)
        if wan:
            allowed.add(key)
    return frozenset(allowed)


def is_email(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_EMAIL_LENGTH:
        return False
    if any(c.isspace() for c in value):
        return False
    local, at, domain = value.partition("@")
    return bool(at and local and domain and "@" not in domain)


class EntitlementClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._http = http
        self._url = base_url + ENTITLEMENTS_PATH
        self._token = token

    async def fetch(self) -> frozenset[str] | None:
        """The allowed emails, or None on any failure. Logs never carry the
        token or the response body (it holds subscriber emails)."""
        try:
            response = await self._http.get(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            logger.warning("Entitlement sync failed: %s", type(exc).__name__)
            return None
        if response.status_code != 200:
            logger.warning("Entitlement sync failed: HTTP %d", response.status_code)
            return None
        if len(response.content) > MAX_RESPONSE_BYTES:
            logger.warning("Entitlement sync failed: response too large")
            return None
        try:
            data = response.json()
        except ValueError:
            data = None
        allowed = parse_entitlements(data)
        if allowed is None:
            logger.warning("Entitlement sync failed: invalid response")
        return allowed


class EntitlementSyncService:
    def __init__(
        self,
        client: EntitlementClient,
        repository: EntitlementSyncRepository,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._repository = repository
        self._now = now

    async def sync_once(self) -> bool:
        """True if the cache was refreshed; False leaves it as it was."""
        allowed = await self._client.fetch()
        if allowed is None:
            return False
        synced_at = self._now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        await self._repository.apply(allowed, synced_at)
        return True


async def run_sync_loop(
    service: EntitlementSyncService, interval_seconds: float = SYNC_INTERVAL_SECONDS
) -> None:
    """Sync now and then every interval_seconds until cancelled. A failed
    or crashing sync never stops the loop."""
    while True:
        try:
            await service.sync_once()
        except Exception:
            logger.exception("Entitlement sync crashed")
        await asyncio.sleep(interval_seconds)
