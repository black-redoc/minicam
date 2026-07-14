"""Integration: the wan_access gate blocks WAN requests without an active
flag but never blocks LAN, backed by a real SQLite database.

get_network_zone is overridden per the plan's guidance instead of faking
client IPs — classify_network itself is covered by tests/unit/test_network.py.
Cloudflare Access verification is exercised through a stubbed
get_cloudflare_access_verifier override, so the JWT cryptography itself
(covered by tests/unit/test_cloudflare_access.py) is not re-tested here.

Login always happens while the zone override is still LAN (the app fixture's
default) and only flips to WAN afterwards: since the login cookie's Secure
flag now follows network_zone too, logging in "as WAN" would mark the cookie
Secure and the plain-http TestClient would then correctly refuse to send it
back on the next request — an unrelated behavior this file isn't testing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.dependencies import get_cloudflare_access_verifier, get_network_zone
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository
from app.services.auth import PasswordHasher
from app.services.cloudflare_access import AccessTokenPayload
from app.services.network import NetworkZone

_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"


def _stub_access_verifier(email: str) -> MagicMock:
    verifier = MagicMock()
    verifier.verify.return_value = AccessTokenPayload(email=email)
    return verifier


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
    state_module.ptz_services["cam1"] = MagicMock()
    try:
        await _login_ptz_user(client, db_connection, "wan-blocked@example.com")
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 403
    finally:
        state_module.ptz_services.clear()


async def test_wan_request_allowed_when_gate_active_and_access_identity_matches(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    email = "wan-active@example.com"
    mock_ptz = MagicMock()
    mock_ptz.is_connected.return_value = True
    state_module.ptz_services["cam1"] = mock_ptz
    try:
        user_id = await _login_ptz_user(client, db_connection, email)
        wan_repo = SQLiteWanAccessRepository(db_connection)
        await wan_repo.set_active(user_id, True)
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: (
            _stub_access_verifier(email)
        )

        res = client.post(
            "/api/ptz/cam1/up", headers={_ACCESS_JWT_HEADER: "fake-signed-token"}
        )
        assert res.status_code == 200
    finally:
        state_module.ptz_services.clear()


async def test_wan_request_blocked_without_access_header_even_when_gate_active(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    email = "wan-no-header@example.com"
    state_module.ptz_services["cam1"] = MagicMock()
    try:
        user_id = await _login_ptz_user(client, db_connection, email)
        wan_repo = SQLiteWanAccessRepository(db_connection)
        await wan_repo.set_active(user_id, True)
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: (
            _stub_access_verifier(email)
        )

        res = client.post("/api/ptz/cam1/up")
        assert res.status_code == 403
    finally:
        state_module.ptz_services.clear()


async def test_wan_request_blocked_when_access_email_does_not_match_session(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    state_module.ptz_services["cam1"] = MagicMock()
    try:
        user_id = await _login_ptz_user(
            client, db_connection, "wan-mismatch@example.com"
        )
        wan_repo = SQLiteWanAccessRepository(db_connection)
        await wan_repo.set_active(user_id, True)
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: (
            _stub_access_verifier("someone-else@example.com")
        )

        res = client.post(
            "/api/ptz/cam1/up", headers={_ACCESS_JWT_HEADER: "fake-signed-token"}
        )
        assert res.status_code == 403
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
