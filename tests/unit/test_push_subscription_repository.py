"""Tests for SQLitePushSubscriptionRepository against an in-memory database."""

from __future__ import annotations

import aiosqlite

from app.repositories.push_subscription_repository import (
    SQLitePushSubscriptionRepository,
)
from app.repositories.user_repository import SQLiteUserRepository


async def _make_user(conn: aiosqlite.Connection, email: str) -> int:
    user = await SQLiteUserRepository(conn).create(email, "hashed", is_admin=False)
    return user.id


async def test_upsert_creates_subscription(db_connection: aiosqlite.Connection) -> None:
    user_id = await _make_user(db_connection, "a@example.com")
    repo = SQLitePushSubscriptionRepository(db_connection)
    sub = await repo.upsert(user_id, "https://fcm.googleapis.com/x", "key", "auth")
    assert (sub.user_id, sub.endpoint, sub.p256dh, sub.auth) == (
        user_id,
        "https://fcm.googleapis.com/x",
        "key",
        "auth",
    )


async def test_upsert_hands_endpoint_to_latest_user(
    db_connection: aiosqlite.Connection,
) -> None:
    first = await _make_user(db_connection, "a@example.com")
    second = await _make_user(db_connection, "b@example.com")
    repo = SQLitePushSubscriptionRepository(db_connection)
    await repo.upsert(first, "https://fcm.googleapis.com/x", "k1", "a1")
    await repo.upsert(second, "https://fcm.googleapis.com/x", "k2", "a2")

    assert await repo.list_for_users([first]) == []
    [sub] = await repo.list_for_users([second])
    assert (sub.p256dh, sub.auth) == ("k2", "a2")


async def test_list_for_users_filters_and_handles_empty(
    db_connection: aiosqlite.Connection,
) -> None:
    first = await _make_user(db_connection, "a@example.com")
    second = await _make_user(db_connection, "b@example.com")
    repo = SQLitePushSubscriptionRepository(db_connection)
    await repo.upsert(first, "https://fcm.googleapis.com/1", "k", "a")
    await repo.upsert(second, "https://fcm.googleapis.com/2", "k", "a")

    assert await repo.list_for_users([]) == []
    assert [s.endpoint for s in await repo.list_for_users([first, second])] == [
        "https://fcm.googleapis.com/1",
        "https://fcm.googleapis.com/2",
    ]


async def test_delete_only_removes_own_subscription(
    db_connection: aiosqlite.Connection,
) -> None:
    owner = await _make_user(db_connection, "a@example.com")
    other = await _make_user(db_connection, "b@example.com")
    repo = SQLitePushSubscriptionRepository(db_connection)
    await repo.upsert(owner, "https://fcm.googleapis.com/x", "k", "a")

    assert await repo.delete(other, "https://fcm.googleapis.com/x") is False
    assert await repo.delete(owner, "https://fcm.googleapis.com/x") is True
    assert await repo.list_for_users([owner]) == []


async def test_delete_by_endpoint(db_connection: aiosqlite.Connection) -> None:
    owner = await _make_user(db_connection, "a@example.com")
    repo = SQLitePushSubscriptionRepository(db_connection)
    await repo.upsert(owner, "https://fcm.googleapis.com/x", "k", "a")
    await repo.delete_by_endpoint("https://fcm.googleapis.com/x")
    assert await repo.list_for_users([owner]) == []


async def test_vapid_key_is_saved_once(db_connection: aiosqlite.Connection) -> None:
    repo = SQLitePushSubscriptionRepository(db_connection)
    assert await repo.get_vapid_private_key() is None
    assert await repo.save_vapid_private_key("first") == "first"
    assert await repo.save_vapid_private_key("second") == "first"
    assert await repo.get_vapid_private_key() == "first"
