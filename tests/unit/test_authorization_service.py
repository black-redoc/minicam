"""Tests for AuthorizationService: deny-by-default matrix."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.models.domain import CameraPermission
from app.services.authorization import AuthorizationService


def _permission(can_view: bool, can_use_ptz: bool) -> CameraPermission:
    return CameraPermission(
        id=1,
        user_id=1,
        camera_name="cam1",
        can_view=can_view,
        can_use_ptz=can_use_ptz,
        created_at="2026-01-01T00:00:00.000Z",
    )


class TestCanView:
    async def test_true_when_permission_grants_view(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = _permission(can_view=True, can_use_ptz=False)
        service = AuthorizationService(repo)
        assert await service.can_view(1, "cam1") is True

    async def test_false_when_permission_denies_view(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = _permission(can_view=False, can_use_ptz=True)
        service = AuthorizationService(repo)
        assert await service.can_view(1, "cam1") is False

    async def test_false_when_no_permission_row(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        service = AuthorizationService(repo)
        assert await service.can_view(1, "cam1") is False


class TestCanUsePtz:
    async def test_true_when_permission_grants_ptz(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = _permission(can_view=True, can_use_ptz=True)
        service = AuthorizationService(repo)
        assert await service.can_use_ptz(1, "cam1") is True

    async def test_false_when_permission_denies_ptz(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = _permission(can_view=True, can_use_ptz=False)
        service = AuthorizationService(repo)
        assert await service.can_use_ptz(1, "cam1") is False

    async def test_false_when_no_permission_row(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        service = AuthorizationService(repo)
        assert await service.can_use_ptz(1, "cam1") is False


class TestListViewableCameraNames:
    async def test_returns_only_viewable_camera_names(self) -> None:
        repo = AsyncMock()
        repo.list_for_user.return_value = [
            CameraPermission(1, 1, "cam1", True, False, "t"),
            CameraPermission(2, 1, "cam2", False, True, "t"),
        ]
        service = AuthorizationService(repo)
        assert await service.list_viewable_camera_names(1) == ["cam1"]

    async def test_returns_empty_list_when_no_permissions(self) -> None:
        repo = AsyncMock()
        repo.list_for_user.return_value = []
        service = AuthorizationService(repo)
        assert await service.list_viewable_camera_names(1) == []
