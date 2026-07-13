"""Integration: the wan_access gate blocks WAN requests without an active
flag but never blocks LAN, backed by a real SQLite database.

get_network_zone is overridden per the plan's guidance instead of faking
client IPs — classify_network itself is covered by tests/unit/test_network.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.dependencies import get_network_zone
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository
from app.services.auth import PasswordHasher
from app.services.network import NetworkZone


async def _login_ptz_user(
    client: TestClient, db_connection: aiosqlite.Connection, email: str
) -> int:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create(email, PasswordHasher().hash("pw"), is_admin=False)
    perm_repo = SQLitePermissionRepository(db_connection)
    await perm_repo.upsert(user.id, "cam1", can_view=True, can_use_ptz=True)
    res = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    assert res.status_code == 200
    return user.id


async def test_wan_request_blocked_when_gate_inactive(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    state_module.ptz_services["cam1"] = MagicMock()
    try:
        await _login_ptz_user(client, db_connection, "wan-blocked@example.com")
        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 403
    finally:
        state_module.ptz_services.clear()


async def test_wan_request_allowed_when_gate_active(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    mock_ptz = MagicMock()
    mock_ptz.is_connected.return_value = True
    state_module.ptz_services["cam1"] = mock_ptz
    try:
        user_id = await _login_ptz_user(client, db_connection, "wan-active@example.com")
        wan_repo = SQLiteWanAccessRepository(db_connection)
        await wan_repo.set_active(user_id, True)

        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 200
    finally:
        state_module.ptz_services.clear()


async def test_lan_request_allowed_even_when_gate_inactive(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    # The `client` fixture already defaults get_network_zone to LAN.
    mock_ptz = MagicMock()
    mock_ptz.is_connected.return_value = True
    state_module.ptz_services["cam1"] = mock_ptz
    try:
        await _login_ptz_user(client, db_connection, "lan-user@example.com")
        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 200
    finally:
        state_module.ptz_services.clear()
