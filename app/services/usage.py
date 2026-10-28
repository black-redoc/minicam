"""Counts WAN live-stream bytes and reports them to the central dashboard
(minicam-admin ADR 0008, minicam ADR 0004).

The stream router adds the length of every WAN stream frame (MJPEG, fMP4,
audio included) to an in-memory UsageMeter. Every FLUSH_INTERVAL_SECONDS the
pending bytes go to SQLite as cumulative counters per (Colombia month, email,
camera); every REPORT_INTERVAL_SECONDS the counters of the current and
previous month that grew since the last accepted report are sent to
POST /api/device/usage. Reports carry totals, not deltas, so a lost or
repeated one is harmless. A crash loses at most the last unflushed minute:
usage is undercounted, never overcounted. Central mode only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta, timezone

import httpx

from app.repositories.usage_repository import UsageCounter, UsageRepository
from app.services.entitlements import REQUEST_TIMEOUT_SECONDS, is_email

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SECONDS = 60.0
REPORT_INTERVAL_SECONDS = 300.0
USAGE_PATH = "/api/device/usage"
# The dashboard takes at most 1000 counters and 256 KiB per report.
MAX_COUNTERS_PER_REPORT = 1000
MAX_REPORT_BYTES = 200 * 1024
MAX_CAMERA_LENGTH = 64
_BOGOTA = timezone(timedelta(hours=-5))  # Colombia has no DST


def bogota_month(now: datetime) -> str:
    """`YYYY-MM` of now in Colombia time."""
    return now.astimezone(_BOGOTA).strftime("%Y-%m")


def previous_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def _is_reportable_camera(name: str) -> bool:
    """What the dashboard accepts: 1-64 characters, none of them control,
    format or unassigned (Unicode category C)."""
    return 0 < len(name) <= MAX_CAMERA_LENGTH and not any(
        unicodedata.category(c).startswith("C") for c in name
    )


class UsageMeter:
    """Pending WAN bytes per (lowercased email, camera), not yet in SQLite."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], int] = {}

    def counter(self, email: str, camera: str) -> Callable[[int], None] | None:
        """A callback adding a frame's byte length to this viewer's counter,
        or None if the dashboard would reject the pair (one bad counter
        rejects a whole report, so it is never stored)."""
        key = (email.strip().lower(), camera)
        if not is_email(key[0]) or not _is_reportable_camera(camera):
            logger.warning("WAN usage not counted: unreportable user or camera")
            return None

        def add(nbytes: int) -> None:
            if nbytes > 0:
                self._pending[key] = self._pending.get(key, 0) + nbytes

        return add

    def pending(self) -> dict[tuple[str, str], int]:
        return dict(self._pending)

    def discard(self, flushed: dict[tuple[str, str], int]) -> None:
        """Drop bytes that are now in SQLite, keeping any counted since."""
        for key, nbytes in flushed.items():
            left = self._pending.get(key, 0) - nbytes
            if left > 0:
                self._pending[key] = left
            else:
                self._pending.pop(key, None)


class UsageClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._http = http
        self._url = base_url + USAGE_PATH
        self._token = token

    async def send(self, reporter_id: str, counters: Sequence[UsageCounter]) -> bool:
        """True only if the dashboard accepted the report (200). Logs never
        carry the token or the counters (they hold user emails)."""
        body = {
            "reporterId": reporter_id,
            "counters": [_counter_json(counter) for counter in counters],
        }
        try:
            response = await self._http.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            logger.warning("Usage report failed: %s", type(exc).__name__)
            return False
        if response.status_code != 200:
            logger.warning("Usage report failed: HTTP %d", response.status_code)
            return False
        return True


def _counter_json(counter: UsageCounter) -> dict[str, object]:
    return {
        "month": counter.month,
        "minicamUser": counter.email,
        "camera": counter.camera,
        "wanBytes": counter.wan_bytes,
    }


def report_chunks(counters: Sequence[UsageCounter]) -> Iterator[list[UsageCounter]]:
    """Consecutive chunks within the dashboard's count and size limits."""
    chunk: list[UsageCounter] = []
    size = 0
    for counter in counters:
        counter_size = len(json.dumps(_counter_json(counter)).encode()) + 1
        if chunk and (
            len(chunk) == MAX_COUNTERS_PER_REPORT
            or size + counter_size > MAX_REPORT_BYTES
        ):
            yield chunk
            chunk, size = [], 0
        chunk.append(counter)
        size += counter_size
    if chunk:
        yield chunk


class UsageService:
    def __init__(
        self,
        meter: UsageMeter,
        repository: UsageRepository,
        client: UsageClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._meter = meter
        self._repository = repository
        self._client = client
        self._now = now
        self._flush_lock = asyncio.Lock()

    async def flush(self) -> int:
        """Move the pending bytes into the current month's SQLite counters;
        they leave the meter only once committed. The number of counters
        written (0 writes nothing)."""
        async with self._flush_lock:
            pending = self._meter.pending()
            if not pending:
                return 0
            await self._repository.add(bogota_month(self._now()), pending)
            self._meter.discard(pending)
            return len(pending)

    async def report(self) -> bool:
        """Send every counter of this and last month that grew since the last
        accepted report; each chunk is marked accepted only on a 200. False
        stops at the first failed chunk, leaving the rest for next time."""
        month = bogota_month(self._now())
        counters = await self._repository.unacked((month, previous_month(month)))
        if not counters:
            return True
        reporter_id = await self._repository.reporter_id()
        for chunk in report_chunks(counters):
            if not await self._client.send(reporter_id, chunk):
                return False
            await self._repository.acknowledge(chunk)
        return True


async def run_usage_loop(
    service: UsageService,
    flush_interval_seconds: float = FLUSH_INTERVAL_SECONDS,
    report_interval_seconds: float = REPORT_INTERVAL_SECONDS,
) -> None:
    """Flush every flush_interval_seconds and report every
    report_interval_seconds until cancelled, then flush one last time. A
    failed or crashing step never stops the loop. Flushes are shielded, so a
    cancel cannot leave bytes both in SQLite and still pending in the meter."""
    last_report = time.monotonic()
    try:
        while True:
            await asyncio.sleep(flush_interval_seconds)
            try:
                await asyncio.shield(service.flush())
            except Exception:
                logger.exception("Usage flush crashed")
            if time.monotonic() - last_report >= report_interval_seconds:
                last_report = time.monotonic()
                try:
                    await service.report()
                except Exception:
                    logger.exception("Usage report crashed")
    finally:
        try:
            await service.flush()
        except Exception:
            logger.exception("Final usage flush failed")
