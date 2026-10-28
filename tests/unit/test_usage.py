"""Tests for WAN usage metering, flushing and reporting
(minicam-admin specs/012 AC6-AC9, ADR 0008)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest

from app.repositories.usage_repository import SQLiteUsageRepository, UsageCounter
from app.services.usage import (
    MAX_COUNTERS_PER_REPORT,
    MAX_REPORT_BYTES,
    USAGE_PATH,
    UsageClient,
    UsageMeter,
    UsageService,
    bogota_month,
    previous_month,
    report_chunks,
    run_usage_loop,
)

_TOKEN = "mcd_00000000-0000-4000-8000-000000000000_" + "s" * 43
_BASE = "https://admin.example.com"
# 2026-10-27 07:00 in Colombia.
_NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


async def _rows(conn: aiosqlite.Connection) -> list[tuple[str, str, str, int, int]]:
    cursor = await conn.execute(
        "SELECT month, email, camera, wan_bytes, acked_bytes FROM usage_counters "
        "ORDER BY month, email, camera"
    )
    return [tuple(row) for row in await cursor.fetchall()]  # type: ignore[misc]


class FakeClient:
    """Records every report; answers from `results` (True = 200), then True."""

    def __init__(self, *results: bool) -> None:
        self.results = list(results)
        self.sent: list[tuple[str, list[UsageCounter]]] = []

    async def send(self, reporter_id: str, counters: list[UsageCounter]) -> bool:
        self.sent.append((reporter_id, list(counters)))
        return self.results.pop(0) if self.results else True


def _service(
    conn: aiosqlite.Connection,
    meter: UsageMeter | None = None,
    client: FakeClient | None = None,
    now: datetime = _NOW,
) -> UsageService:
    return UsageService(
        meter or UsageMeter(),
        SQLiteUsageRepository(conn),
        client or FakeClient(),  # type: ignore[arg-type]
        now=lambda: now,
    )


class TestMonths:
    def test_month_follows_colombia_time(self) -> None:
        assert bogota_month(_NOW) == "2026-10"
        # 2026-11-01 03:00 UTC is still October in Colombia.
        assert bogota_month(datetime(2026, 11, 1, 3, tzinfo=UTC)) == "2026-10"
        assert bogota_month(datetime(2026, 11, 1, 5, tzinfo=UTC)) == "2026-11"

    def test_previous_month(self) -> None:
        assert previous_month("2026-10") == "2026-09"
        assert previous_month("2026-01") == "2025-12"


class TestUsageMeter:
    def test_counts_bytes_per_lowercased_email_and_camera(self) -> None:
        meter = UsageMeter()
        ana_patio = meter.counter(" Ana@Example.com ", "Patio")
        ana_sala = meter.counter("ana@example.com", "Sala")
        assert ana_patio is not None and ana_sala is not None
        ana_patio(100)
        ana_patio(20)
        ana_sala(5)
        ana_sala(0)
        assert meter.pending() == {
            ("ana@example.com", "Patio"): 120,
            ("ana@example.com", "Sala"): 5,
        }

    @pytest.mark.parametrize(
        ("email", "camera"),
        [
            ("ana", "Patio"),
            ("a b@example.com", "Patio"),
            ("ana@example.com", ""),
            ("ana@example.com", "x" * 65),
            ("ana@example.com", "Patio\n"),
        ],
    )
    def test_unreportable_pairs_are_not_counted(
        self, email: str, camera: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The dashboard rejects a whole report over one bad counter.
        with caplog.at_level(logging.WARNING):
            assert UsageMeter().counter(email, camera) is None
        assert "WAN usage not counted" in caplog.text

    def test_discard_keeps_bytes_counted_since(self) -> None:
        meter = UsageMeter()
        add = meter.counter("ana@example.com", "Patio")
        assert add is not None
        add(100)
        flushed = meter.pending()
        add(7)
        meter.discard(flushed)
        assert meter.pending() == {("ana@example.com", "Patio"): 7}
        meter.discard(meter.pending())
        assert meter.pending() == {}


async def test_usage_flush(db_connection: aiosqlite.Connection) -> None:
    meter = UsageMeter()
    service = _service(db_connection, meter)
    assert await service.flush() == 0
    assert await _rows(db_connection) == []

    ana, beto = (
        meter.counter("ana@example.com", "Patio"),
        meter.counter("beto@example.com", "Patio"),
    )
    assert ana is not None and beto is not None
    ana(100)
    beto(10)
    assert await service.flush() == 2
    ana(50)
    assert await service.flush() == 1
    assert await service.flush() == 0
    assert meter.pending() == {}
    assert await _rows(db_connection) == [
        ("2026-10", "ana@example.com", "Patio", 150, 0),
        ("2026-10", "beto@example.com", "Patio", 10, 0),
    ]


async def test_usage_flush_keeps_pending_bytes_when_the_write_fails(
    db_connection: aiosqlite.Connection,
) -> None:
    meter = UsageMeter()
    repo = AsyncMock()
    repo.add.side_effect = aiosqlite.OperationalError("database is locked")
    service = UsageService(meter, repo, FakeClient(), now=lambda: _NOW)  # type: ignore[arg-type]
    add = meter.counter("ana@example.com", "Patio")
    assert add is not None
    add(100)
    with pytest.raises(aiosqlite.OperationalError):
        await service.flush()
    assert meter.pending() == {("ana@example.com", "Patio"): 100}


async def test_failed_add_writes_nothing(db_connection: aiosqlite.Connection) -> None:
    repo = SQLiteUsageRepository(db_connection)
    await repo.add("2026-10", {("ana@example.com", "Patio"): 5})
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.add(
            "2026-10",
            {("ana@example.com", "Patio"): 10, ("beto@example.com", "Patio"): -1},
        )
    assert await _rows(db_connection) == [("2026-10", "ana@example.com", "Patio", 5, 0)]


async def test_reporter_id_is_created_once(db_connection: aiosqlite.Connection) -> None:
    repo = SQLiteUsageRepository(db_connection)
    reporter_id = await repo.reporter_id()
    assert uuid.UUID(reporter_id).version == 4
    assert await SQLiteUsageRepository(db_connection).reporter_id() == reporter_id


async def test_usage_report_sends_grown_counters(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUsageRepository(db_connection)
    await repo.add("2026-08", {("ana@example.com", "Patio"): 1})  # too old to report
    await repo.add("2026-09", {("ana@example.com", "Patio"): 70})
    await repo.add("2026-10", {("ana@example.com", "Patio"): 100})
    await repo.add("2026-10", {("beto@example.com", "Patio"): 10})
    client = FakeClient()
    service = _service(db_connection, client=client)

    assert await service.report() is True
    reporter_id = await repo.reporter_id()
    assert client.sent == [
        (
            reporter_id,
            [
                UsageCounter("2026-09", "ana@example.com", "Patio", 70),
                UsageCounter("2026-10", "ana@example.com", "Patio", 100),
                UsageCounter("2026-10", "beto@example.com", "Patio", 10),
            ],
        )
    ]

    # Only counters that grew since are sent again; nothing grew, nothing is sent.
    await repo.add("2026-10", {("ana@example.com", "Patio"): 5})
    assert await service.report() is True
    assert client.sent[1] == (
        reporter_id,
        [UsageCounter("2026-10", "ana@example.com", "Patio", 105)],
    )
    assert await service.report() is True
    assert len(client.sent) == 2
    assert await _rows(db_connection) == [
        ("2026-08", "ana@example.com", "Patio", 1, 0),
        ("2026-09", "ana@example.com", "Patio", 70, 70),
        ("2026-10", "ana@example.com", "Patio", 105, 105),
        ("2026-10", "beto@example.com", "Patio", 10, 10),
    ]


async def test_usage_report_sends_large_backlogs_in_chunks(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUsageRepository(db_connection)
    await repo.add(
        "2026-10",
        {(f"user{i:04d}@example.com", "Patio"): i + 1 for i in range(2500)},
    )
    client = FakeClient()
    assert await _service(db_connection, client=client).report() is True
    assert [len(counters) for _, counters in client.sent] == [1000, 1000, 500]
    assert await repo.unacked(("2026-10",)) == []


async def test_usage_report_keeps_unacked_on_failure(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUsageRepository(db_connection)
    await repo.add(
        "2026-10",
        {(f"user{i:04d}@example.com", "Patio"): i + 1 for i in range(1500)},
    )
    client = FakeClient(True, False)
    service = _service(db_connection, client=client)

    assert await service.report() is False
    # The first chunk was accepted, the second was not and stays pending.
    assert len(await repo.unacked(("2026-10",))) == 500
    assert await service.report() is True
    assert len(client.sent) == 3
    assert client.sent[2][1] == client.sent[1][1]
    assert await repo.unacked(("2026-10",)) == []


def test_report_chunks_respect_the_size_limit() -> None:
    long = [
        UsageCounter("2026-10", f"{'a' * 240}{i:04d}@x.co", "📷" * 64, 2**53 - 1)
        for i in range(MAX_COUNTERS_PER_REPORT)
    ]
    chunks = list(report_chunks(long))
    assert len(chunks) > 1
    assert [c for chunk in chunks for c in chunk] == long
    for chunk in chunks:
        body = json.dumps(
            {
                "reporterId": str(uuid.uuid4()),
                "counters": [
                    {
                        "month": c.month,
                        "minicamUser": c.email,
                        "camera": c.camera,
                        "wanBytes": c.wan_bytes,
                    }
                    for c in chunk
                ],
            }
        )
        assert len(body.encode()) <= MAX_REPORT_BYTES + 1024
    assert list(report_chunks([])) == []


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> UsageClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return UsageClient(http, _BASE, _TOKEN)


class TestUsageClient:
    async def test_posts_the_report_with_the_device_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"accepted": 1})

        counters = [UsageCounter("2026-10", "ana@example.com", "Patio", 100)]
        assert await _client(handler).send("r-1", counters) is True
        assert str(seen[0].url) == _BASE + USAGE_PATH
        assert seen[0].method == "POST"
        assert seen[0].headers["authorization"] == f"Bearer {_TOKEN}"
        assert json.loads(seen[0].content) == {
            "reporterId": "r-1",
            "counters": [
                {
                    "month": "2026-10",
                    "minicamUser": "ana@example.com",
                    "camera": "Patio",
                    "wanBytes": 100,
                }
            ],
        }

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(400, text="Bad Request"),
            httpx.Response(401, text="Unauthorized"),
            httpx.Response(500),
            httpx.Response(302, headers={"location": "https://evil.example.com"}),
        ],
    )
    async def test_non_200_is_a_failed_report(
        self, response: httpx.Response, caplog: pytest.LogCaptureFixture
    ) -> None:
        counters = [UsageCounter("2026-10", "ana@example.com", "Patio", 100)]
        with caplog.at_level(logging.WARNING):
            assert await _client(lambda _r: response).send("r-1", counters) is False
        assert "Usage report failed" in caplog.text
        assert _TOKEN not in caplog.text
        assert "ana@example.com" not in caplog.text

    async def test_network_errors_are_a_failed_report(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with caplog.at_level(logging.WARNING):
            assert await _client(handler).send("r-1", []) is False
        assert "ConnectError" in caplog.text
        assert _TOKEN not in caplog.text


async def test_usage_loop_survives_failures() -> None:
    service = AsyncMock()
    flushes = reports = 0

    async def flush() -> int:
        nonlocal flushes
        flushes += 1
        if flushes == 1:
            raise RuntimeError("database locked")
        return 0

    async def report() -> bool:
        nonlocal reports
        reports += 1
        if reports == 1:
            raise RuntimeError("boom")
        return False

    service.flush.side_effect = flush
    service.report.side_effect = report
    task = asyncio.create_task(
        run_usage_loop(service, flush_interval_seconds=0.001, report_interval_seconds=0)
    )
    while reports < 3:
        await asyncio.sleep(0.001)
    before = flushes
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    # Shutdown flushes one last time.
    assert flushes == before + 1


async def test_usage_loop_waits_for_the_intervals() -> None:
    service = AsyncMock()
    service.flush.return_value = 0
    task = asyncio.create_task(
        run_usage_loop(
            service, flush_interval_seconds=0.01, report_interval_seconds=3600
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert service.flush.await_count >= 2
    service.report.assert_not_awaited()


async def test_cancel_during_a_flush_does_not_double_count(
    db_connection: aiosqlite.Connection,
) -> None:
    meter = UsageMeter()
    repo = SQLiteUsageRepository(db_connection)
    started = asyncio.Event()
    real_add = repo.add

    async def slow_add(month: str, pending: dict[tuple[str, str], int]) -> None:
        started.set()
        await asyncio.sleep(0.02)
        await real_add(month, pending)

    repo.add = slow_add  # type: ignore[method-assign]
    service = UsageService(meter, repo, FakeClient(), now=lambda: _NOW)  # type: ignore[arg-type]
    add = meter.counter("ana@example.com", "Patio")
    assert add is not None
    add(100)
    task = asyncio.create_task(
        run_usage_loop(service, flush_interval_seconds=0, report_interval_seconds=3600)
    )
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert await _rows(db_connection) == [
        ("2026-10", "ana@example.com", "Patio", 100, 0)
    ]
    assert meter.pending() == {}


async def test_usage_loop_logs_a_failed_final_flush(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = AsyncMock()
    service.flush.side_effect = RuntimeError("database closed")
    task = asyncio.create_task(run_usage_loop(service, flush_interval_seconds=3600))
    await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert "Final usage flush failed" in caplog.text
