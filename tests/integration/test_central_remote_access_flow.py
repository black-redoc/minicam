"""Integration: pairing with the central dashboard also installs the tunnel it
hands over (ADR 0010, minicam-admin ADR 0012), through the same helper path as
the app's own setup (ADR 0008).
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

from app import state
from app.dependencies import get_cloudflare_access_verifier, get_tunnel_service
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import system as system_router
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher
from app.services.central import CentralTunnel, PairedSite, PairingError
from app.services.central_config import CentralSettings
from app.services.tunnel import AccessSettings, TunnelService, read_tunnel_settings
from tests.unit.test_tunnel_service import AUD, TEAM, make_token

URL = "https://admin.example.com"
BODY = {"url": URL, "pairing_code": "abcd-efgh"}
HOSTNAME = "cam.example.com"


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list[CentralSettings] = []

    async def start(self, settings: CentralSettings) -> None:
        self.started.append(settings)

    async def stop(self) -> None:
        pass


class FakeRedeemer:
    def __init__(self) -> None:
        self.token = make_token()
        self.tunnel: CentralTunnel | None = CentralTunnel(
            token=self.token,
            access=AccessSettings(hostname=HOSTNAME, team_domain=TEAM, aud=AUD),
        )
        self.invalid = False
        self.error: str | None = None

    async def __call__(self, url: str, code: str) -> PairedSite:
        if self.error:
            raise PairingError(self.error)
        return PairedSite(
            name="Casa",
            hostname=HOSTNAME,
            device_token="mcd_secret_token",
            tunnel=None if self.invalid else self.tunnel,
            tunnel_invalid=self.invalid,
        )


@pytest.fixture
def helper_dir(isolated_tunnel_dir: Path) -> Path:
    helper = isolated_tunnel_dir / ".tunnel"
    helper.mkdir()
    return helper


@pytest.fixture(autouse=True)
def runtime() -> Iterator[FakeRuntime]:
    fake = FakeRuntime()
    state.central_runtime = fake  # type: ignore[assignment]
    yield fake
    state.central_runtime = None


@pytest.fixture
def redeemer(
    app: FastAPI, isolated_tunnel_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeRedeemer]:
    for name in (
        "MINICAM_CENTRAL_URL",
        "MINICAM_DEVICE_TOKEN",
        "MINICAM_CF_ACCESS_TEAM_DOMAIN",
        "MINICAM_CF_ACCESS_AUD",
    ):
        monkeypatch.delenv(name, raising=False)
    get_security_settings.cache_clear()
    get_cloudflare_access_verifier.cache_clear()
    fake = FakeRedeemer()
    app.include_router(system_router.router)
    app.dependency_overrides[system_router.get_pairing_redeemer] = lambda: fake
    app.dependency_overrides[get_tunnel_service] = lambda: TunnelService(
        isolated_tunnel_dir
    )
    yield fake
    get_security_settings.cache_clear()
    get_cloudflare_access_verifier.cache_clear()


@pytest.fixture
async def admin(client: TestClient, db_connection: aiosqlite.Connection) -> None:
    await SQLiteUserRepository(db_connection).create(
        "admin@example.com", PasswordHasher().hash("pw"), is_admin=True
    )
    res = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "pw"}
    )
    assert res.status_code == 200


def _request(helper_dir: Path) -> dict[str, str] | None:
    path = helper_dir / "request.json"
    return json.loads(path.read_text()) if path.exists() else None


def _app_tunnel(client: TestClient) -> None:
    body = {
        "tunnel_token": make_token(),
        "hostname": "own.example.org",
        "team_domain": TEAM,
        "aud": "b" * 64,
    }
    assert client.post("/api/system/tunnel", json=body).status_code == 202


@pytest.mark.usefixtures("admin")
async def test_central_pairing_installs_tunnel(
    client: TestClient,
    redeemer: FakeRedeemer,
    helper_dir: Path,
    isolated_tunnel_dir: Path,
) -> None:
    res = client.post("/api/system/central", json=BODY)

    assert res.status_code == 200
    assert res.json()["connected"] is True
    assert res.json()["tunnel"] == {
        "state": "installing",
        "hostname": HOSTNAME,
        "message": None,
        "replaced": False,
    }
    assert _request(helper_dir) == {"action": "install", "token": redeemer.token}
    assert (helper_dir / "request.json").stat().st_mode & 0o777 == 0o600
    assert (isolated_tunnel_dir / "tunnel.json").stat().st_mode & 0o777 == 0o600
    settings = read_tunnel_settings(isolated_tunnel_dir)
    assert settings is not None and settings.source == "central"
    # The Access settings apply at once, and loopback (cloudflared) is WAN.
    security = get_security_settings()
    assert (security.cf_access_team_domain, security.cf_access_aud) == (TEAM, AUD)

    status = client.get("/api/system/tunnel").json()
    assert (status["state"], status["hostname"], status["source"]) == (
        "installing",
        HOSTNAME,
        "central",
    )


@pytest.mark.usefixtures("admin", "helper_dir")
async def test_central_pairing_validates_tunnel(
    client: TestClient, redeemer: FakeRedeemer, isolated_tunnel_dir: Path
) -> None:
    redeemer.invalid = True

    res = client.post("/api/system/central", json=BODY)

    assert res.status_code == 200
    assert res.json()["connected"] is True
    assert res.json()["tunnel"]["state"] == "error"
    assert read_tunnel_settings(isolated_tunnel_dir) is None


@pytest.mark.usefixtures("admin")
async def test_central_pairing_without_tunnel(
    client: TestClient, redeemer: FakeRedeemer, helper_dir: Path
) -> None:
    _app_tunnel(client)
    (helper_dir / "request.json").unlink()
    redeemer.tunnel = None

    res = client.post("/api/system/central", json=BODY)

    assert res.json()["tunnel"] is None
    assert _request(helper_dir) is None
    assert client.get("/api/system/tunnel").json()["source"] == "app"


@pytest.mark.usefixtures("admin")
async def test_central_pairing_skips_tunnel_with_env_access(
    client: TestClient,
    redeemer: FakeRedeemer,
    helper_dir: Path,
    isolated_tunnel_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MINICAM_CF_ACCESS_TEAM_DOMAIN", "https://own.cloudflareaccess.com"
    )
    monkeypatch.setenv("MINICAM_CF_ACCESS_AUD", "c" * 64)

    res = client.post("/api/system/central", json=BODY)

    assert res.json()["tunnel"]["state"] == "skipped"
    assert _request(helper_dir) is None
    assert read_tunnel_settings(isolated_tunnel_dir) is None


@pytest.mark.usefixtures("admin")
async def test_central_pairing_without_helper(
    client: TestClient, redeemer: FakeRedeemer, isolated_tunnel_dir: Path
) -> None:
    res = client.post("/api/system/central", json=BODY)

    assert res.json()["connected"] is True
    assert res.json()["tunnel"]["state"] == "error"
    assert read_tunnel_settings(isolated_tunnel_dir) is None


@pytest.mark.usefixtures("admin")
async def test_central_pairing_replaces_app_tunnel(
    client: TestClient,
    redeemer: FakeRedeemer,
    helper_dir: Path,
    isolated_tunnel_dir: Path,
) -> None:
    _app_tunnel(client)

    res = client.post("/api/system/central", json=BODY)

    assert res.json()["tunnel"]["replaced"] is True
    assert _request(helper_dir) == {"action": "install", "token": redeemer.token}
    settings = read_tunnel_settings(isolated_tunnel_dir)
    assert settings is not None and settings.access is not None
    assert (settings.source, settings.access.hostname) == ("central", HOSTNAME)


@pytest.mark.usefixtures("admin", "helper_dir")
async def test_tunnel_endpoints_locked_when_central(
    client: TestClient, redeemer: FakeRedeemer
) -> None:
    client.post("/api/system/central", json=BODY)

    body = {
        "tunnel_token": make_token(),
        "hostname": "own.example.org",
        "team_domain": TEAM,
        "aud": AUD,
    }
    for res in (
        client.post("/api/system/tunnel", json=body),
        client.delete("/api/system/tunnel"),
    ):
        assert res.status_code == 409
        assert "central dashboard" in res.json()["detail"]


@pytest.mark.usefixtures("admin")
async def test_unpair_removes_central_tunnel(
    client: TestClient,
    redeemer: FakeRedeemer,
    helper_dir: Path,
    isolated_tunnel_dir: Path,
) -> None:
    client.post("/api/system/central", json=BODY)
    (helper_dir / "request.json").unlink()

    assert client.delete("/api/system/central").status_code == 200

    assert _request(helper_dir) == {"action": "remove"}
    settings = read_tunnel_settings(isolated_tunnel_dir)
    assert settings is not None and settings.access is None
    assert get_security_settings().cf_access_aud is None


@pytest.mark.usefixtures("admin")
async def test_unpair_keeps_app_tunnel(
    client: TestClient, redeemer: FakeRedeemer, helper_dir: Path
) -> None:
    redeemer.tunnel = None
    client.post("/api/system/central", json=BODY)
    _app_tunnel(client)
    (helper_dir / "request.json").unlink()

    client.delete("/api/system/central")

    assert _request(helper_dir) is None
    assert client.get("/api/system/tunnel").json()["source"] == "app"


@pytest.mark.usefixtures("admin")
async def test_repair_reinstalls_tunnel(
    client: TestClient, redeemer: FakeRedeemer, helper_dir: Path
) -> None:
    client.post("/api/system/central", json=BODY)
    (helper_dir / "request.json").unlink()

    res = client.post("/api/system/central", json=BODY)

    assert res.json()["tunnel"]["state"] == "installing"
    assert res.json()["tunnel"]["replaced"] is False
    assert _request(helper_dir) == {"action": "install", "token": redeemer.token}


@pytest.mark.usefixtures("admin", "helper_dir")
async def test_central_tunnel_token_not_logged(
    client: TestClient,
    redeemer: FakeRedeemer,
    isolated_tunnel_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        res = client.post("/api/system/central", json=BODY)
        status = client.get("/api/system/tunnel")

    for text in (caplog.text, res.text, status.text):
        assert redeemer.token not in text
    assert redeemer.token not in (isolated_tunnel_dir / "tunnel.json").read_text()
