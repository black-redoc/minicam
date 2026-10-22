"""Per-camera authorization: deny-by-default, no row means no access."""

from __future__ import annotations

from app.models.domain import CameraPermission
from app.repositories.permission_repository import PermissionRepository


class AuthorizationService:
    def __init__(self, permission_repository: PermissionRepository) -> None:
        self._permission_repository = permission_repository

    async def can_view(self, user_id: int, camera_name: str) -> bool:
        permission = await self._permission_repository.get(user_id, camera_name)
        return permission is not None and permission.can_view

    async def can_use_ptz(self, user_id: int, camera_name: str) -> bool:
        permission = await self._permission_repository.get(user_id, camera_name)
        return permission is not None and permission.can_use_ptz

    async def list_viewable_permissions(self, user_id: int) -> list[CameraPermission]:
        permissions = await self._permission_repository.list_for_user(user_id)
        return [p for p in permissions if p.can_view]

    async def list_viewable_camera_names(self, user_id: int) -> list[str]:
        return [p.camera_name for p in await self.list_viewable_permissions(user_id)]
