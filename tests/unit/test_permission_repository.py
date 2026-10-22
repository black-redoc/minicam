"""Tests for SQLitePermissionRepository against an in-memory database."""

from __future__ import annotations

import aiosqlite

from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository


async def _make_user(db_connection: aiosqlite.Connection) -> int:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create("perm-owner@example.com", "hashed", is_admin=False)
    return user.id


async def test_get_returns_none_for_unset_permission(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLitePermissionRepository(db_connection)
    assert await repo.get(user_id, "cam1") is None


async def test_upsert_creates_permission(db_connection: aiosqlite.Connection) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLitePermissionRepository(db_connection)
    perm = await repo.upsert(user_id, "cam1", can_view=True, can_use_ptz=False)
    assert perm.user_id == user_id
    assert perm.camera_name == "cam1"
    assert perm.can_view is True
    assert perm.can_use_ptz is False


async def test_upsert_updates_existing_permission(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLitePermissionRepository(db_connection)
    first = await repo.upsert(user_id, "cam1", can_view=True, can_use_ptz=False)
    second = await repo.upsert(user_id, "cam1", can_view=True, can_use_ptz=True)
    assert first.id == second.id
    assert second.can_use_ptz is True


async def test_list_for_user_returns_all_permissions(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLitePermissionRepository(db_connection)
    await repo.upsert(user_id, "cam1", can_view=True, can_use_ptz=False)
    await repo.upsert(user_id, "cam2", can_view=False, can_use_ptz=True)
    permissions = await repo.list_for_user(user_id)
    assert {p.camera_name for p in permissions} == {"cam1", "cam2"}


async def test_list_for_user_empty_when_no_permissions(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLitePermissionRepository(db_connection)
    assert await repo.list_for_user(user_id) == []


async def test_list_viewer_ids_returns_active_viewers_only(
    db_connection: aiosqlite.Connection,
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    viewer = await user_repo.create("viewer@example.com", "h", is_admin=False)
    ptz_only = await user_repo.create("ptz@example.com", "h", is_admin=False)
    inactive = await user_repo.create("inactive@example.com", "h", is_admin=False)
    await db_connection.execute(
        "UPDATE users SET is_active = 0 WHERE id = ?", (inactive.id,)
    )
    repo = SQLitePermissionRepository(db_connection)
    await repo.upsert(viewer.id, "cam1", can_view=True, can_use_ptz=False)
    await repo.upsert(ptz_only.id, "cam1", can_view=False, can_use_ptz=True)
    await repo.upsert(inactive.id, "cam1", can_view=True, can_use_ptz=False)
    await repo.upsert(viewer.id, "cam2", can_view=True, can_use_ptz=False)

    assert await repo.list_viewer_ids("cam1") == [viewer.id]
    assert await repo.list_viewer_ids("missing") == []
