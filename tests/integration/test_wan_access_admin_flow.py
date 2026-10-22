"""Integration: admins grant and revoke a user's WAN access through
/api/auth/wan-access (the Admin page checkbox), backed by a real SQLite
database, and the grant actually opens the WAN gate.

As in test_wan_access_gate_flow.py, the WAN zone and the Access verifier are
overridden instead of faking client IPs or signing real Access JWTs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.dependencies import get_cloudflare_access_verifier, get_network_zone
from app.models.domain import User
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository
from app.services.auth import PasswordHasher
from app.services.cloudflare_access import AccessTokenPayload
from app.services.network import NetworkZone


async def _create_user(
    db_connection: aiosqlite.Connection, email: str, *, is_admin: bool
) -> User:
    return await SQLiteUserRepository(db_connection).create(
        email, PasswordHasher().hash("pw"), is_admin=is_admin
    )


def _login(client: TestClient, email: str) -> None:
    res = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    assert res.status_code == 200


async def test_requires_admin(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    assert client.get("/api/auth/wan-access?user_id=1").status_code == 401

    user = await _create_user(db_connection, "viewer@example.com", is_admin=False)
    _login(client, "viewer@example.com")

    assert client.get(f"/api/auth/wan-access?user_id={user.id}").status_code == 403
    res = client.post(
        "/api/auth/wan-access", json={"user_id": user.id, "is_active": True}
    )
    assert res.status_code == 403
    assert await SQLiteWanAccessRepository(db_connection).get(user.id) is None


async def test_defaults_to_inactive_without_a_row(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "admin@example.com", is_admin=True)
    viewer = await _create_user(db_connection, "viewer@example.com", is_admin=False)
    _login(client, "admin@example.com")

    res = client.get(f"/api/auth/wan-access?user_id={viewer.id}")

    assert res.status_code == 200
    assert res.json() == {"user_id": viewer.id, "is_active": False}


async def test_grant_and_revoke(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "admin@example.com", is_admin=True)
    viewer = await _create_user(db_connection, "viewer@example.com", is_admin=False)
    _login(client, "admin@example.com")

    granted = client.post(
        "/api/auth/wan-access", json={"user_id": viewer.id, "is_active": True}
    )
    assert granted.status_code == 200
    assert granted.json() == {"user_id": viewer.id, "is_active": True}
    assert client.get(f"/api/auth/wan-access?user_id={viewer.id}").json()["is_active"]

    revoked = client.post(
        "/api/auth/wan-access", json={"user_id": viewer.id, "is_active": False}
    )
    assert revoked.json() == {"user_id": viewer.id, "is_active": False}
    stored = await SQLiteWanAccessRepository(db_connection).get(viewer.id)
    assert stored is not None
    assert stored.is_active is False


async def test_unknown_user_returns_404(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "admin@example.com", is_admin=True)
    _login(client, "admin@example.com")

    res = client.post("/api/auth/wan-access", json={"user_id": 999, "is_active": True})

    assert res.status_code == 404


async def test_grant_opens_the_wan_gate(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    """The bug this feature fixes: a user hitting the tunnel got 403 until
    someone wrote to wan_access by hand."""
    admin = await _create_user(db_connection, "admin@example.com", is_admin=True)
    await SQLitePermissionRepository(db_connection).upsert(
        admin.id, "cam1", can_view=True, can_use_ptz=False
    )
    _login(client, "admin@example.com")
    verifier = MagicMock()
    verifier.verify.return_value = AccessTokenPayload(email="admin@example.com")
    app.dependency_overrides[get_cloudflare_access_verifier] = lambda: verifier
    state_module.cameras["cam1"] = MagicMock()
    headers = {"Cf-Access-Jwt-Assertion": "fake-signed-token"}
    try:
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        assert client.post("/api/stream/cam1/token", headers=headers).status_code == 403

        # Granting happens from the LAN Admin page; then WAN works.
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
        client.post(
            "/api/auth/wan-access", json={"user_id": admin.id, "is_active": True}
        )
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN

        assert client.post("/api/stream/cam1/token", headers=headers).status_code == 200
    finally:
        state_module.cameras.clear()
