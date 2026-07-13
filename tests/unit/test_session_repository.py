"""Tests for SQLiteSessionRepository against an in-memory database."""

from __future__ import annotations

import aiosqlite

from app.repositories.session_repository import SQLiteSessionRepository
from app.repositories.user_repository import SQLiteUserRepository


async def _make_user(db_connection: aiosqlite.Connection) -> int:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create("session-owner@example.com", "hashed", is_admin=False)
    return user.id


async def test_create_returns_persisted_session(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteSessionRepository(db_connection)
    session = await repo.create(
        "jti-1", user_id, "2099-01-01T00:00:00.000Z", "pytest-agent", "127.0.0.1"
    )
    assert session.jti == "jti-1"
    assert session.user_id == user_id
    assert session.revoked_at is None
    assert session.user_agent == "pytest-agent"
    assert session.ip_address == "127.0.0.1"


async def test_get_returns_none_when_missing(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteSessionRepository(db_connection)
    assert await repo.get("missing-jti") is None


async def test_get_returns_created_session(
    db_connection: aiosqlite.Connection,
) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteSessionRepository(db_connection)
    created = await repo.create(
        "jti-2", user_id, "2099-01-01T00:00:00.000Z", None, None
    )
    fetched = await repo.get("jti-2")
    assert fetched == created


async def test_revoke_sets_revoked_at(db_connection: aiosqlite.Connection) -> None:
    user_id = await _make_user(db_connection)
    repo = SQLiteSessionRepository(db_connection)
    await repo.create("jti-3", user_id, "2099-01-01T00:00:00.000Z", None, None)
    await repo.revoke("jti-3")
    fetched = await repo.get("jti-3")
    assert fetched is not None
    assert fetched.revoked_at is not None


async def test_revoke_on_unknown_jti_is_a_no_op(
    db_connection: aiosqlite.Connection,
) -> None:
    repo = SQLiteSessionRepository(db_connection)
    await repo.revoke("does-not-exist")  # must not raise
