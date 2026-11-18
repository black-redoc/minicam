"""Integration: an admin checks for and starts a minicam update through
/api/system/update (minicam-mobile, ADR 0013)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_network_zone, get_update_service
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import system as system_router
from app.services.auth import PasswordHasher
from app.services.network import NetworkZone
from app.services.update import UpdateService
from tests.unit.test_update_service import release


@pytest.fixture
def update_dir(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
    (tmp_path / ".update").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def update_app(app: FastAPI, update_dir: Path) -> Iterator[FastAPI]:
    async def fetch() -> dict[str, Any]:
        return release("v0.7.0")

    service = UpdateService(update_dir, fetch_release=fetch)
    app.include_router(system_router.router)
    app.dependency_overrides[get_update_service] = lambda: service
    yield app


async def _login(
    client: TestClient, db_connection: aiosqlite.Connection, *, is_admin: bool
) -> None:
    email = "admin@example.com" if is_admin else "viewer@example.com"
    await SQLiteUserRepository(db_connection).create(
        email, PasswordHasher().hash("pw"), is_admin=is_admin
    )
    res = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    assert res.status_code == 200


async def test_update_requires_an_admin(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    assert client.get("/api/system/update").status_code == 401
    await _login(client, db_connection, is_admin=False)
    assert client.get("/api/system/update").status_code == 403
    res = client.post("/api/system/update", json={"version": "v0.7.0"})
    assert res.status_code == 403


async def test_admin_sees_and_starts_an_update(
    client: TestClient, db_connection: aiosqlite.Connection, update_dir: Path
) -> None:
    await _login(client, db_connection, is_admin=True)

    status = client.get("/api/system/update").json()
    assert status["current"] == "v0.6.0"
    assert status["latest"] == "v0.7.0"
    assert status["update_available"] is True
    assert status["state"] == "idle"

    res = client.post("/api/system/update", json={"version": "v0.7.0"})
    assert res.status_code == 202
    assert res.json()["state"] == "requested"
    request = json.loads((update_dir / ".update" / "request.json").read_text())
    assert request == {"action": "install", "version": "v0.7.0"}


async def test_updates_work_through_the_tunnel_too(
    app: FastAPI, client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _login(client, db_connection, is_admin=True)
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN

    assert client.get("/api/system/update").status_code == 200


async def test_other_versions_are_refused(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _login(client, db_connection, is_admin=True)
    client.get("/api/system/update")

    res = client.post("/api/system/update", json={"version": "v0.9.0"})
    assert res.status_code == 409
    assert res.json()["detail"] == "That version is not an available update"
    bad = client.post("/api/system/update", json={"version": "latest"})
    assert bad.status_code == 422


async def test_without_the_helper_there_is_nothing_to_install(
    client: TestClient, db_connection: aiosqlite.Connection, update_dir: Path
) -> None:
    (update_dir / ".update").rmdir()
    await _login(client, db_connection, is_admin=True)

    status = client.get("/api/system/update").json()
    assert status["available"] is False
    assert status["update_available"] is False
    assert status["latest"] is None  # GitHub is not even asked
    res = client.post("/api/system/update", json={"version": "v0.7.0"})
    assert res.status_code == 409
