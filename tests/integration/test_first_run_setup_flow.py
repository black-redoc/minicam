"""First-run setup against a real SQLite database: the first visitor creates
the admin account, and from then on only the normal login is offered.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_network_zone
from app.services.network import NetworkZone

_OWNER = {"email": "owner@example.com", "password": "owner-password"}


async def test_first_run_creates_admin_then_only_login_is_offered(
    client: TestClient,
) -> None:
    assert client.get("/api/auth/setup").json() == {"setup_required": True}
    assert 'id="setup-form"' in client.get("/login").text

    res = client.post("/api/auth/setup", json=_OWNER)
    assert res.status_code == 201
    assert res.json()["is_admin"] is True
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == _OWNER["email"]

    assert client.get("/api/auth/setup").json() == {"setup_required": False}
    page = client.get("/login").text
    assert 'id="login-form"' in page
    assert 'id="setup-form"' not in page

    client.post("/api/auth/logout")
    login = client.post("/api/auth/login", json=_OWNER)
    assert login.status_code == 200


async def test_setup_cannot_run_twice(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json=_OWNER).status_code == 201

    second = client.post(
        "/api/auth/setup",
        json={"email": "intruder@example.com", "password": "intruder-password"},
    )
    assert second.status_code == 409
    login = client.post(
        "/api/auth/login",
        json={"email": "intruder@example.com", "password": "intruder-password"},
    )
    assert login.status_code == 401


async def test_setup_is_refused_from_wan(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN

    res = client.post("/api/auth/setup", json=_OWNER)

    assert res.status_code == 403
    assert client.get("/api/auth/setup").json() == {"setup_required": True}
