"""Tests for WanAccessGateService: deny-by-default WAN gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.models.domain import WanAccess
from app.services.wan_access import MAX_SYNC_AGE, WanAccessGateService


class TestIsActive:
    async def test_true_when_row_is_active(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = WanAccess(
            user_id=1, is_active=True, updated_at="2026-01-01T00:00:00.000Z"
        )
        service = WanAccessGateService(repo)
        assert await service.is_active(1) is True

    async def test_false_when_row_is_inactive(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = WanAccess(
            user_id=1, is_active=False, updated_at="2026-01-01T00:00:00.000Z"
        )
        service = WanAccessGateService(repo)
        assert await service.is_active(1) is False

    async def test_false_when_no_row(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        service = WanAccessGateService(repo)
        assert await service.is_active(1) is False


_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _central(is_active: bool, synced_at: str | None) -> WanAccessGateService:
    repo = AsyncMock()
    repo.get.return_value = WanAccess(user_id=1, is_active=is_active, updated_at="t")
    sync = AsyncMock()
    sync.last_synced_at.return_value = synced_at
    return WanAccessGateService(repo, sync, now=lambda: _NOW)


class TestCentralMode:
    """minicam-admin specs/011 AC8: the cache is only trusted while fresh."""

    @pytest.mark.parametrize(
        ("synced_at", "expected"),
        [
            (_iso(_NOW), True),
            (_iso(_NOW - timedelta(minutes=1)), True),
            (_iso(_NOW - MAX_SYNC_AGE), True),
            (_iso(_NOW - MAX_SYNC_AGE - timedelta(seconds=1)), False),
            (_iso(_NOW - timedelta(days=3)), False),
            (_iso(_NOW + timedelta(seconds=1)), False),  # clock jumped back
            ("2026-10-26T11:59:00", True),  # no offset: read as UTC
            ("not a time", False),
            (None, False),  # never synced
        ],
    )
    async def test_wan_gate_fails_closed(
        self, synced_at: str | None, expected: bool
    ) -> None:
        assert await _central(True, synced_at).is_active(1) is expected

    async def test_inactive_flag_stays_denied_when_fresh(self) -> None:
        assert await _central(False, _iso(_NOW)).is_active(1) is False

    async def test_standalone_mode_ignores_sync_time(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = WanAccess(user_id=1, is_active=True, updated_at="t")
        assert await WanAccessGateService(repo).is_active(1) is True
