"""Tests for SQLiteUserRepository against an in-memory database."""

from __future__ import annotations

import aiosqlite
import pytest

from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.session_repository import SQLiteSessionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository


async def test_create_returns_persisted_user(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    user = await repo.create("admin@example.com", "hashed", is_admin=True)
    assert user.id > 0
    assert user.email == "admin@example.com"
    assert user.password_hash == "hashed"
    assert user.is_admin is True
    assert user.is_active is True


async def test_get_by_id_returns_none_when_missing(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    assert await repo.get_by_id(999) is None


async def test_get_by_id_returns_created_user(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    created = await repo.create("viewer@example.com", "hashed", is_admin=False)
    fetched = await repo.get_by_id(created.id)
    assert fetched == created


async def test_get_by_email_returns_none_when_missing(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    assert await repo.get_by_email("ghost@example.com") is None


async def test_get_by_email_returns_created_user(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    created = await repo.create("viewer2@example.com", "hashed", is_admin=False)
    fetched = await repo.get_by_email("viewer2@example.com")
    assert fetched == created


async def test_email_uniqueness_is_enforced(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    await repo.create("dup@example.com", "hashed", is_admin=False)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create("dup@example.com", "hashed2", is_admin=False)


async def test_list_all_returns_empty_list_when_no_users(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    assert await repo.list_all() == []


async def test_list_all_returns_every_created_user(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    await repo.create("b@example.com", "hashed", is_admin=False)
    await repo.create("a@example.com", "hashed", is_admin=True)
    users = await repo.list_all()
    assert [u.email for u in users] == ["a@example.com", "b@example.com"]


async def test_count_admins_returns_zero_when_none(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    await repo.create("viewer@example.com", "hashed", is_admin=False)
    assert await repo.count_admins() == 0


async def test_count_admins_counts_only_admin_accounts(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    await repo.create("admin1@example.com", "hashed", is_admin=True)
    await repo.create("admin2@example.com", "hashed", is_admin=True)
    await repo.create("viewer@example.com", "hashed", is_admin=False)
    assert await repo.count_admins() == 2


async def test_delete_removes_the_user(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    user = await repo.create("gone@example.com", "hashed", is_admin=False)
    await repo.delete(user.id)
    assert await repo.get_by_id(user.id) is None


async def test_delete_is_idempotent_for_a_missing_user(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteUserRepository(db_connection)
    await repo.delete(999)  # must not raise


async def test_delete_cascades_to_sessions_permissions_and_wan_access(
    db_connection: aiosqlite.Connection,
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    session_repo = SQLiteSessionRepository(db_connection)
    permission_repo = SQLitePermissionRepository(db_connection)
    wan_access_repo = SQLiteWanAccessRepository(db_connection)

    user = await user_repo.create("cascade@example.com", "hashed", is_admin=False)
    await session_repo.create("jti-1", user.id, "2099-01-01T00:00:00.000Z", None, None)
    await permission_repo.upsert(user.id, "cam1", can_view=True, can_use_ptz=False)
    await wan_access_repo.set_active(user.id, True)

    await user_repo.delete(user.id)

    assert await session_repo.get("jti-1") is None
    assert await permission_repo.get(user.id, "cam1") is None
    assert await wan_access_repo.get(user.id) is None
