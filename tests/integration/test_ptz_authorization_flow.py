"""Integration: PTZ command endpoint enforces can_use_ptz, backed by a real
SQLite database. Missing permission returns 404, not 403 — see
require_can_use_ptz in app/dependencies.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
from fastapi.testclient import TestClient

import app.state as state_module
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.services.auth import PasswordHasher


async def _login_with_permission(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    email: str,
    can_use_ptz: bool,
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create(email, PasswordHasher().hash("pw"), is_admin=False)
    perm_repo = SQLitePermissionRepository(db_connection)
    await perm_repo.upsert(user.id, "cam1", can_view=True, can_use_ptz=can_use_ptz)
    login_res = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    assert login_res.status_code == 200


async def test_returns_401_when_not_authenticated(client: TestClient) -> None:
    res = client.post("/api/ptz/cam1/up")
    assert res.status_code == 401


async def test_returns_404_without_ptz_permission(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    state_module.ptz_services["cam1"] = MagicMock()
    try:
        await _login_with_permission(
            client, db_connection, "noptz@example.com", can_use_ptz=False
        )
        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 404
    finally:
        state_module.ptz_services.clear()


async def test_returns_200_with_ptz_permission(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    mock_ptz = MagicMock()
    mock_ptz.is_connected.return_value = True
    state_module.ptz_services["cam1"] = mock_ptz
    try:
        await _login_with_permission(
            client, db_connection, "ptz@example.com", can_use_ptz=True
        )
        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 200
        mock_ptz.run_command.assert_called_once_with("up")
    finally:
        state_module.ptz_services.clear()


async def test_returns_404_for_a_camera_with_no_permission_row_at_all(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    state_module.ptz_services["cam-unlisted"] = MagicMock()
    try:
        user_repo = SQLiteUserRepository(db_connection)
        await user_repo.create(
            "bystander@example.com", PasswordHasher().hash("pw"), is_admin=False
        )
        client.post(
            "/api/auth/login",
            json={"email": "bystander@example.com", "password": "pw"},
        )
        res = client.post("/api/ptz/cam-unlisted/up")
        assert res.status_code == 404
    finally:
        state_module.ptz_services.clear()
