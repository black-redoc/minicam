"""Tests for the central entitlement client, sync service and loop
(minicam-admin specs/011 AC6, AC9)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.entitlements import (
    ENTITLEMENTS_PATH,
    MAX_RESPONSE_BYTES,
    EntitlementClient,
    EntitlementSyncService,
    parse_entitlements,
    run_sync_loop,
)

_TOKEN = "mcd_00000000-0000-4000-8000-000000000000_" + "s" * 43
_BASE = "https://admin.example.com"


def _body(*users: dict[str, Any]) -> dict[str, Any]:
    return {
        "month": "2026-10",
        "generatedAt": "2026-10-26T12:00:00.000Z",
        "users": list(users),
    }


_ANA = {"minicamUser": "Ana@Example.com", "wan": True, "reason": "ok"}
_BETO = {"minicamUser": "beto@example.com", "wan": False, "reason": "unpaid"}
_CARO = {"minicamUser": "caro@example.com", "wan": False, "reason": "manual_block"}


class TestParseEntitlements:
    def test_returns_the_lowercased_allowed_emails(self) -> None:
        assert parse_entitlements(_body(_ANA, _BETO, _CARO)) == {"ana@example.com"}
        assert parse_entitlements(_body()) == frozenset()

    @pytest.mark.parametrize(
        "data",
        [
            None,
            [],
            "ok",
            {**_body(_ANA), "users": None},
            {**_body(_ANA), "users": {"a": 1}},
            {k: v for k, v in _body(_ANA).items() if k != "month"},
            {**_body(_ANA), "generatedAt": 1},
            _body("ana@example.com"),
            _body({**_ANA, "minicamUser": "ana"}),
            _body({**_ANA, "minicamUser": "a b@example.com"}),
            _body({**_ANA, "minicamUser": "a@b@c"}),
            _body({**_ANA, "minicamUser": "@example.com"}),
            _body({**_ANA, "minicamUser": "a" * 250 + "@x.co"}),
            _body({**_ANA, "minicamUser": 7}),
            _body({**_ANA, "wan": "true"}),
            _body({**_ANA, "wan": 1}),
            _body({**_ANA, "reason": "paid"}),
            _body({**_ANA, "reason": "unpaid"}),  # wan contradicts the reason
            _body({**_BETO, "wan": True}),
            _body(_ANA, {**_ANA, "minicamUser": "ana@EXAMPLE.com"}),  # listed twice
        ],
    )
    def test_rejects_bad_payloads(self, data: object) -> None:
        assert parse_entitlements(data) is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> EntitlementClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EntitlementClient(http, _BASE, _TOKEN)


class TestEntitlementClient:
    async def test_fetches_with_the_device_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_body(_ANA, _BETO))

        assert await _client(handler).fetch() == {"ana@example.com"}
        assert str(seen[0].url) == _BASE + ENTITLEMENTS_PATH
        assert seen[0].method == "GET"
        assert seen[0].headers["authorization"] == f"Bearer {_TOKEN}"

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(401, text="Unauthorized"),
            httpx.Response(500),
            httpx.Response(302, headers={"location": "https://evil.example.com"}),
            httpx.Response(200, text="not json"),
            httpx.Response(200, json={"users": "nope"}),
            httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1)),
        ],
    )
    async def test_entitlement_client_rejects_bad_payloads(
        self, response: httpx.Response, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            assert await _client(lambda _request: response).fetch() is None
        assert "Entitlement sync failed" in caplog.text
        assert _TOKEN not in caplog.text

    async def test_network_errors_are_a_failed_sync(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with caplog.at_level(logging.WARNING):
            assert await _client(handler).fetch() is None
        assert "ConnectError" in caplog.text
        assert _TOKEN not in caplog.text


class TestEntitlementSyncService:
    async def test_applies_a_good_answer_with_the_sync_time(self) -> None:
        client = AsyncMock()
        client.fetch.return_value = frozenset({"ana@example.com"})
        repo = AsyncMock()
        now = datetime(2026, 10, 26, 12, 0, 0, 123456, tzinfo=UTC)
        service = EntitlementSyncService(client, repo, now=lambda: now)

        assert await service.sync_once() is True
        repo.apply.assert_awaited_once_with(
            frozenset({"ana@example.com"}), "2026-10-26T12:00:00.123Z"
        )

    async def test_failed_fetch_keeps_the_cache(self) -> None:
        client = AsyncMock()
        client.fetch.return_value = None
        repo = AsyncMock()
        assert await EntitlementSyncService(client, repo).sync_once() is False
        repo.apply.assert_not_awaited()


async def test_sync_loop_survives_failures() -> None:
    service = AsyncMock()
    calls = 0

    async def sync_once() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database locked")
        return calls % 2 == 0

    service.sync_once.side_effect = sync_once
    task = asyncio.create_task(run_sync_loop(service, interval_seconds=0.001))
    while calls < 4:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_sync_loop_syncs_at_once_then_waits() -> None:
    service = AsyncMock()
    service.sync_once.return_value = True
    task = asyncio.create_task(run_sync_loop(service, interval_seconds=3600))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert service.sync_once.await_count == 1

