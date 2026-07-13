"""Tests for SQLiteWanAccessRepository against an in-memory database."""

from __future__ import annotations

import aiosqlite

from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository


async def _make_user(db_connection: aiosqlite.Connection) -> int:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create("wan-owner@example.com", "hashed", is_admin=False)
    return user.id


async def test_get_returns_none_when_no_row(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteWanAccessRepository(db_connection)
    assert await repo.get(user_id) is None


async def test_set_active_creates_row(db_connection: aiosqlite.Connection) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteWanAccessRepository(db_connection)
    wan_access = await repo.set_active(user_id, True)
    assert wan_access.user_id == user_id
    assert wan_access.is_active is True


async def test_set_active_updates_existing_row(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteWanAccessRepository(db_connection)
    await repo.set_active(user_id, True)
    updated = await repo.set_active(user_id, False)
    assert updated.is_active is False


async def test_get_returns_persisted_row(db_connection: aiosqlite.Connection) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteWanAccessRepository(db_connection)
    await repo.set_active(user_id, True)
    fetched = await repo.get(user_id)
    assert fetched is not None
    assert fetched.is_active is True
