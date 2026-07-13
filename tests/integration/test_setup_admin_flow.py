"""Integration: every route under /api/cameras and /api/setup is admin-only,
backed by a real SQLite database.
"""

from __future__ import annotations

import aiosqlite
from fastapi.testclient import TestClient

from app.repositories.user_repository import SQLiteUserRepository
from app.services.auth import PasswordHasher


async def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    res = client.get("/api/cameras")
    assert res.status_code == 401


async def test_non_admin_request_returns_403(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    await user_repo.create(
        "regular@example.com", PasswordHasher().hash("pw"), is_admin=False
    )
    client.post(
        "/api/auth/login", json={"email": "regular@example.com", "password": "pw"}
    )

    res = client.get("/api/cameras")
    assert res.status_code == 403


async def test_admin_request_succeeds(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    await user_repo.create(
        "setup-admin@example.com", PasswordHasher().hash("pw"), is_admin=True
    )
    client.post(
        "/api/auth/login", json={"email": "setup-admin@example.com", "password": "pw"}
    )

    res = client.get("/api/cameras")
    assert res.status_code == 200
