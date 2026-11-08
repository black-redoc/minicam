"""Connection to the central dashboard (minicam-admin), set up from the app (ADR 0009).

An admin on the LAN gives minicam the dashboard's address and a one-time
pairing code shown there (minicam-admin ADR 0011). minicam exchanges the code
itself (POST /api/device/pair) for its device token, so the token never
passes through a person or the phone, stores both in central.json (0600) and
starts the entitlement sync and usage reports without a restart.

MINICAM_CENTRAL_URL / MINICAM_DEVICE_TOKEN in .env still win: an install
configured there is never changed from the app.

With remote access provisioning on (minicam-admin ADR 0012), the same answer
carries the site's tunnel settings, which minicam installs (ADR 0010).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import aiosqlite
import httpx

from app import state
from app.repositories.entitlement_sync_repository import SQLiteEntitlementSyncRepository
from app.repositories.usage_repository import SQLiteUsageRepository
from app.services.central_config import CentralSettings
from app.services.entitlements import (
    EntitlementClient,
    EntitlementSyncService,
    run_sync_loop,
)
from app.services.tunnel import (
    AccessSettings,
    is_valid_aud,
    is_valid_hostname,
    is_valid_team_domain,
    is_valid_tunnel_token,
)
from app.services.usage import UsageClient, UsageMeter, UsageService, run_usage_loop

logger = logging.getLogger(__name__)

PAIR_PATH = "/api/device/pair"
PAIR_TIMEOUT_SECONDS = 15.0
MAX_PAIR_RESPONSE_BYTES = 16 * 1024


class PairingError(Exception):
    """A failed pairing; the message is safe to show to the admin."""


@dataclass(frozen=True, slots=True)
class CentralTunnel:
    """The site's tunnel from the dashboard, already validated like
    POST /api/system/tunnel input (ADR 0008)."""

    token: str = field(repr=False)
    access: AccessSettings


@dataclass(frozen=True, slots=True)
class PairedSite:
    name: str
    hostname: str
    device_token: str = field(repr=False)
    tunnel: CentralTunnel | None = None
    # The dashboard sent tunnel settings that failed validation: the pairing
    # still holds, the tunnel is not installed.
    tunnel_invalid: bool = False


def parse_central_tunnel(raw: object) -> CentralTunnel | None:
    """The pair answer's `tunnel`, or None when it is invalid."""
    if not isinstance(raw, dict):
        return None
    token, hostname = raw.get("tunnel_token"), raw.get("hostname")
    team_domain, aud = raw.get("team_domain"), raw.get("aud")
    if not (
        isinstance(token, str)
        and isinstance(hostname, str)
        and isinstance(team_domain, str)
        and isinstance(aud, str)
    ):
        return None
    if not (
        is_valid_tunnel_token(token)
        and is_valid_hostname(hostname)
        and is_valid_team_domain(team_domain)
        and is_valid_aud(aud)
    ):
        return None
    return CentralTunnel(
        token=token,
        access=AccessSettings(hostname=hostname, team_domain=team_domain, aud=aud),
    )


async def redeem_pairing_code(
    http: httpx.AsyncClient, base_url: str, code: str
) -> PairedSite:
    """Exchange the dashboard's one-time code for this device's token."""
    try:
        response = await http.post(
            base_url + PAIR_PATH, json={"code": code}, timeout=PAIR_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        raise PairingError("Can't reach the dashboard at that address") from exc
    if response.status_code == 401:
        raise PairingError(
            "The dashboard did not accept the code. Codes work once and for "
            "10 minutes: show a new one with Pair device."
        )
    if response.status_code == 503:
        raise PairingError(
            "The dashboard could not read this site's remote access right now. "
            "Try again: the code still works."
        )
    if response.status_code != 200 or len(response.content) > MAX_PAIR_RESPONSE_BYTES:
        raise PairingError(f"The dashboard answered {response.status_code}")
    try:
        data = response.json()
        token, site = data["token"], data["site"]
        name, hostname = site["name"], site["hostname"]
    except (ValueError, TypeError, KeyError) as exc:
        raise PairingError("The dashboard sent an unexpected answer") from exc
    if not all(isinstance(v, str) and v for v in (token, name, hostname)):
        raise PairingError("The dashboard sent an unexpected answer")
    raw_tunnel = data.get("tunnel")
    tunnel = parse_central_tunnel(raw_tunnel) if raw_tunnel is not None else None
    return PairedSite(
        name=name,
        hostname=hostname,
        device_token=token,
        tunnel=tunnel,
        tunnel_invalid=raw_tunnel is not None and tunnel is None,
    )


class CentralRuntime:
    """Runs the entitlement sync and usage report loops for one dashboard,
    and restarts them when the app pairs the device again."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._http: httpx.AsyncClient | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.url: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._tasks)

    async def start(self, settings: CentralSettings) -> None:
        await self.stop()
        self._http = httpx.AsyncClient()
        sync_service = EntitlementSyncService(
            EntitlementClient(self._http, settings.url, settings.device_token),
            SQLiteEntitlementSyncRepository(self._conn),
        )
        state.usage_meter = UsageMeter()
        usage_service = UsageService(
            state.usage_meter,
            SQLiteUsageRepository(self._conn),
            UsageClient(self._http, settings.url, settings.device_token),
        )
        self._tasks = [
            asyncio.create_task(run_sync_loop(sync_service)),
            asyncio.create_task(run_usage_loop(usage_service)),
        ]
        self.url = settings.url
        logger.info("Central mode: syncing WAN entitlements and reporting WAN usage")

    async def stop(self) -> None:
        """Cancelling the usage loop flushes the last counted bytes."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        state.usage_meter = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self.url = None
