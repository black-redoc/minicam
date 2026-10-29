"""Integration: an admin on the LAN sets up and removes the Cloudflare Tunnel
through /api/system/tunnel (minicam-mobile, ADR 0008), and the Access
settings it hands over apply without a restart.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import (
    get_cloudflare_access_verifier,
    get_network_zone,
    get_tunnel_service,
)
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import system as system_router
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher
from app.services.network import NetworkZone
from app.services.tunnel import TunnelService
from tests.unit.test_tunnel_service import AUD, TEAM, make_token


@pytest.fixture
def helper_dir(isolated_tunnel_dir: Path) -> Path:
    helper = isolated_tunnel_dir / ".tunnel"
    helper.mkdir()
    return helper


@pytest.fixture(autouse=True)
def tunnel_app(app: FastAPI, isolated_tunnel_dir: Path) -> Iterator[FastAPI]:
    app.include_router(system_router.router)
    app.dependency_overrides[get_tunnel_service] = lambda: TunnelService(
        isolated_tunnel_dir
    )
    get_cloudflare_access_verifier.cache_clear()
    yield app
    get_cloudflare_access_verifier.cache_clear()


async def _login(
    client: TestClient, db_connection: aiosqlite.Connection, *, is_admin: bool
) -> None:
    email = "admin@example.com" if is_admin else "viewer@example.com"
    await SQLiteUserRepository(db_connection).create(
        email, PasswordHasher().hash("pw"), is_admin=is_admin
    )
    res = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    assert res.status_code == 200


def _body(**overrides: str) -> dict[str, str]:
    body = {
        "tunnel_token": make_token(),
        "hostname": "cam.example.com",
        "team_domain": TEAM,
        "aud": AUD,
    }
    body.update(overrides)
    return body


async def test_tunnel_requires_admin_on_lan(
    app: FastAPI,
    client: TestClient,
    db_connection: aiosqlite.Connection,
    helper_dir: Path,
) -> None:
    assert client.get("/api/system/tunnel").status_code == 401

    await _login(client, db_connection, is_admin=False)
    assert client.get("/api/system/tunnel").status_code == 403
    assert client.post("/api/system/tunnel", json=_body()).status_code == 403

    client.post("/api/auth/logout")
    await _login(client, db_connection, is_admin=True)
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    assert client.get("/api/system/tunnel").status_code == 403
    assert client.delete("/api/system/tunnel").status_code == 403
    assert not (helper_dir / "request.json").exists()


async def test_tunnel_unavailable_without_helper(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _login(client, db_connection, is_admin=True)
    assert client.get("/api/system/tunnel").status_code == 404
    assert client.post("/api/system/tunnel", json=_body()).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"tunnel_token": "nope"},
        {"hostname": "localhost"},
        {"team_domain": "https://evil.example.com"},
        {"aud": "short"},
    ],
)
async def test_tunnel_setup_validates_input(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    helper_dir: Path,
    overrides: dict[str, str],
) -> None:
    await _login(client, db_connection, is_admin=True)
    res = client.post("/api/system/tunnel", json=_body(**overrides))
    assert res.status_code == 422
    assert not (helper_dir / "request.json").exists()


async def test_tunnel_setup_writes_request_and_applies_access(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    helper_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINICAM_TRUSTED_PROXIES", raising=False)
    await _login(client, db_connection, is_admin=True)
    assert get_cloudflare_access_verifier() is None

    body = _body()
    res = client.post("/api/system/tunnel", json=body)

    assert res.status_code == 202
    request = json.loads((helper_dir / "request.json").read_text())
    assert request == {"action": "install", "token": body["tunnel_token"]}
    settings = get_security_settings()
    assert settings.cf_access_aud == AUD
    assert "127.0.0.1" in settings.trusted_proxies
    assert get_cloudflare_access_verifier() is not None

    status = client.get("/api/system/tunnel").json()
    assert status == {
        "available": True,
        "state": "installing",
        "hostname": "cam.example.com",
        "message": None,
    }


async def test_tunnel_status(
    client: TestClient, db_connection: aiosqlite.Connection, helper_dir: Path
) -> None:
    await _login(client, db_connection, is_admin=True)
    (helper_dir / "status.json").write_text(
        json.dumps({"state": "error", "message": "No internet connection"})
    )
    status = client.get("/api/system/tunnel").json()
    assert status["state"] == "error"
    assert status["message"] == "No internet connection"


async def test_tunnel_remove(
    client: TestClient, db_connection: aiosqlite.Connection, helper_dir: Path
) -> None:
    await _login(client, db_connection, is_admin=True)
    assert client.post("/api/system/tunnel", json=_body()).status_code == 202
    (helper_dir / "request.json").unlink()

    res = client.delete("/api/system/tunnel")

    assert res.status_code == 202
    assert json.loads((helper_dir / "request.json").read_text()) == {"action": "remove"}
    assert get_security_settings().cf_access_aud is None
    assert get_cloudflare_access_verifier() is None


async def test_tunnel_token_not_logged(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    helper_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _login(client, db_connection, is_admin=True)
    body = _body()
    with caplog.at_level(logging.DEBUG):
        assert client.post("/api/system/tunnel", json=body).status_code == 202
        status = client.get("/api/system/tunnel").text

    assert body["tunnel_token"] not in caplog.text
    assert body["tunnel_token"] not in status
    assert "cam.example.com" in caplog.text
