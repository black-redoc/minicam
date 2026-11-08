"""Integration: an admin on the LAN pairs the device with the central dashboard
through /api/system/central (minicam-mobile, ADR 0009), without a restart.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import state
from app.dependencies import get_network_zone
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import system as system_router
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher
from app.services.central import PairedSite, PairingError
from app.services.central_config import CentralSettings, read_central_file
from app.services.network import NetworkZone

URL = "https://admin.example.com"
BODY = {"url": URL + "/", "pairing_code": "abcd-efgh"}


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list[CentralSettings] = []
        self.stopped = 0

    async def start(self, settings: CentralSettings) -> None:
        self.started.append(settings)

    async def stop(self) -> None:
        self.stopped += 1


class FakeRedeemer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error: str | None = None

    async def __call__(self, url: str, code: str) -> PairedSite:
        self.calls.append((url, code))
        if self.error:
            raise PairingError(self.error)
        return PairedSite(
            name="Casa", hostname="cam.example.com", device_token="mcd_secret_token"
        )


@pytest.fixture
def runtime() -> Iterator[FakeRuntime]:
    fake = FakeRuntime()
    state.central_runtime = fake  # type: ignore[assignment]
    yield fake
    state.central_runtime = None


@pytest.fixture
def redeemer(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRedeemer]:
    monkeypatch.delenv("MINICAM_CENTRAL_URL", raising=False)
    monkeypatch.delenv("MINICAM_DEVICE_TOKEN", raising=False)
    get_security_settings.cache_clear()
    fake = FakeRedeemer()
    app.include_router(system_router.router)
    app.dependency_overrides[system_router.get_pairing_redeemer] = lambda: fake
    yield fake
    get_security_settings.cache_clear()


async def _login(
    client: TestClient, db: aiosqlite.Connection, *, is_admin: bool
) -> None:
    email = "admin@example.com" if is_admin else "viewer@example.com"
    await SQLiteUserRepository(db).create(
        email, PasswordHasher().hash("pw"), is_admin=is_admin
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": email, "password": "pw"}
        ).status_code
        == 200
    )


async def test_central_requires_admin_on_lan(
    app: FastAPI,
    client: TestClient,
    db_connection: aiosqlite.Connection,
    runtime: FakeRuntime,
    redeemer: FakeRedeemer,
) -> None:
    assert client.get("/api/system/central").status_code == 401
    await _login(client, db_connection, is_admin=False)
    assert client.post("/api/system/central", json=BODY).status_code == 403

    client.post("/api/auth/logout")
    await _login(client, db_connection, is_admin=True)
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    assert client.post("/api/system/central", json=BODY).status_code == 403
    assert redeemer.calls == [] and runtime.started == []


async def test_pairing_stores_the_token_and_starts_syncing(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    runtime: FakeRuntime,
    redeemer: FakeRedeemer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _login(client, db_connection, is_admin=True)
    assert client.get("/api/system/central").json() == {
        "connected": False,
        "url": None,
        "source": None,
        "last_synced_at": None,
        "site": None,
    }

    with caplog.at_level(logging.DEBUG):
        res = client.post("/api/system/central", json=BODY)

    assert res.status_code == 200
    assert res.json() == {
        "connected": True,
        "url": URL,
        "source": "app",
        "last_synced_at": None,
        "site": {"name": "Casa", "hostname": "cam.example.com"},
    }
    assert redeemer.calls == [(URL, "abcd-efgh")]
    assert read_central_file() == CentralSettings(URL, "mcd_secret_token")
    assert runtime.started == [CentralSettings(URL, "mcd_secret_token")]
    assert get_security_settings().central_mode
    for secret in ("mcd_secret_token", "abcd-efgh"):
        assert secret not in caplog.text
        assert secret not in res.text
    assert client.get("/api/system/central").json()["source"] == "app"


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://admin.example.com", "pairing_code": "abcd-efgh"},
        {"url": URL + "/path", "pairing_code": "abcd-efgh"},
        {"url": URL, "pairing_code": "ab"},
        {"url": URL, "pairing_code": "abcd;efgh"},
    ],
)
async def test_pairing_validates_input(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    runtime: FakeRuntime,
    redeemer: FakeRedeemer,
    body: dict[str, str],
) -> None:
    await _login(client, db_connection, is_admin=True)
    assert client.post("/api/system/central", json=body).status_code == 422
    assert redeemer.calls == []


async def test_pairing_errors_are_reported(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    runtime: FakeRuntime,
    redeemer: FakeRedeemer,
) -> None:
    await _login(client, db_connection, is_admin=True)
    redeemer.error = "The dashboard did not accept the code."

    res = client.post("/api/system/central", json=BODY)

    assert res.status_code == 502
    assert res.json()["detail"] == "The dashboard did not accept the code."
    assert read_central_file() is None
    assert runtime.started == []


async def test_env_configuration_cannot_be_changed_from_the_app(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    runtime: FakeRuntime,
    redeemer: FakeRedeemer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINICAM_CENTRAL_URL", "https://env.example.com")
    monkeypatch.setenv("MINICAM_DEVICE_TOKEN", "mcd_env")
    get_security_settings.cache_clear()
    await _login(client, db_connection, is_admin=True)

    assert client.get("/api/system/central").json()["source"] == "env"
    assert client.post("/api/system/central", json=BODY).status_code == 409
    assert client.delete("/api/system/central").status_code == 409
    assert redeemer.calls == []


async def test_unpairing_stops_syncing(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    runtime: FakeRuntime,
    redeemer: FakeRedeemer,
) -> None:
    await _login(client, db_connection, is_admin=True)
    client.post("/api/system/central", json=BODY)

    res = client.delete("/api/system/central")

    assert res.status_code == 200
    assert res.json()["connected"] is False
    assert runtime.stopped == 1
    assert read_central_file() is None
    assert not get_security_settings().central_mode


async def test_pairing_before_startup_is_refused(
    client: TestClient,
    db_connection: aiosqlite.Connection,
    redeemer: FakeRedeemer,
) -> None:
    state.central_runtime = None
    await _login(client, db_connection, is_admin=True)
    assert client.post("/api/system/central", json=BODY).status_code == 503
