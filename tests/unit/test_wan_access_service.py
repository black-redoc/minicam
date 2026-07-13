"""Tests for WanAccessGateService: deny-by-default WAN gate."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.models.domain import WanAccess
from app.services.wan_access import WanAccessGateService


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
