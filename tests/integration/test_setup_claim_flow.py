"""Integration: with the LED claim (MINICAM_SETUP_CLAIM=led, ADR 0006) the
first admin needs the code the device's LED blinks, asked for from the LAN
within the setup window.
"""

from __future__ import annotations

import logging

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_network_zone, get_setup_claim_service
from app.repositories.user_repository import SQLiteUserRepository
from app.services.auth import PasswordHasher
from app.services.network import NetworkZone
from app.services.setup_claim import (
    CODE_INTERVAL_SECONDS,
    MAX_CODES_PER_WINDOW,
    WINDOW_SECONDS,
    LedUnavailableError,
    SetupClaimService,
)
from tests.unit.test_setup_claim import FakeClock, FakeLed, make_service

ADMIN = {"email": "admin@example.com", "password": "correct horse"}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def led() -> FakeLed:
    return FakeLed()


@pytest.fixture
def service(app: FastAPI, clock: FakeClock, led: FakeLed) -> SetupClaimService:
    claim = make_service(clock, led, digits="2468")
    app.dependency_overrides[get_setup_claim_service] = lambda: claim
    return claim


def test_setup_status_reports_claim_window(
    app: FastAPI, client: TestClient, service: SetupClaimService, clock: FakeClock
) -> None:
    status = client.get("/api/auth/setup").json()
    assert status == {
        "setup_required": True,
        "claim": "led",
        "window_open": True,
        "window_seconds_left": WINDOW_SECONDS,
    }

    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    assert client.get("/api/auth/setup").json()["window_open"] is False

    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
    clock.now += WINDOW_SECONDS
    status = client.get("/api/auth/setup").json()
    assert status["window_open"] is False
    assert status["window_seconds_left"] == 0


async def test_setup_status_closed_once_a_user_exists(
    client: TestClient, service: SetupClaimService, db_connection: aiosqlite.Connection
) -> None:
    await SQLiteUserRepository(db_connection).create(
        "x@example.com", PasswordHasher().hash("pw"), is_admin=True
    )
    status = client.get("/api/auth/setup").json()
    assert status["setup_required"] is False
    assert status["window_open"] is False
    assert client.post("/api/auth/setup/claim").status_code == 409


def test_setup_claim_starts_led_code(
    client: TestClient, service: SetupClaimService, led: FakeLed
) -> None:
    res = client.post("/api/auth/setup/claim")
    assert res.status_code == 200
    assert res.json() == {"digits": 4, "expires_in": 300}
    assert led.shown == ["2468"]
    assert "2468" not in res.text


def test_setup_claim_rejected_outside_window(
    app: FastAPI, client: TestClient, service: SetupClaimService, clock: FakeClock
) -> None:
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    assert client.post("/api/auth/setup/claim").status_code == 403

    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
    clock.now += WINDOW_SECONDS
    res = client.post("/api/auth/setup/claim")
    assert res.status_code == 403
    assert "Restart" in res.json()["detail"]


def test_setup_claim_rate_limited(
    client: TestClient, service: SetupClaimService, clock: FakeClock
) -> None:
    assert client.post("/api/auth/setup/claim").status_code == 200
    assert client.post("/api/auth/setup/claim").status_code == 429
    for _ in range(MAX_CODES_PER_WINDOW - 1):
        clock.now += CODE_INTERVAL_SECONDS
        assert client.post("/api/auth/setup/claim").status_code == 200
    clock.now += CODE_INTERVAL_SECONDS
    assert client.post("/api/auth/setup/claim").status_code == 403


def test_setup_claim_without_led(app: FastAPI, client: TestClient) -> None:
    class BrokenLed(FakeLed):
        async def show(self, code: str) -> None:
            raise LedUnavailableError("The device's LED can't be controlled")

    claim = make_service(FakeClock(), BrokenLed())
    app.dependency_overrides[get_setup_claim_service] = lambda: claim
    assert client.post("/api/auth/setup/claim").status_code == 503


def test_setup_claim_not_offered_without_led_mode(client: TestClient) -> None:
    assert client.post("/api/auth/setup/claim").status_code == 404


def test_setup_requires_valid_led_code(
    client: TestClient, service: SetupClaimService
) -> None:
    assert client.post("/api/auth/setup", json=ADMIN).status_code == 403

    client.post("/api/auth/setup/claim")
    assert client.post("/api/auth/setup", json=ADMIN).status_code == 403
    for wrong in ("1111", "1112"):
        res = client.post("/api/auth/setup", json={**ADMIN, "setup_code": wrong})
        assert res.status_code == 403
    res = client.post("/api/auth/setup", json={**ADMIN, "setup_code": "2468"})
    assert res.status_code == 403
    assert client.get("/api/auth/setup").json()["setup_required"] is True

    res = client.post("/api/auth/setup", json={**ADMIN, "setup_code": "12a4"})
    assert res.status_code == 422


def test_setup_with_led_code_creates_admin(
    client: TestClient, service: SetupClaimService, led: FakeLed
) -> None:
    client.post("/api/auth/setup/claim")

    res = client.post("/api/auth/setup", json={**ADMIN, "setup_code": "2468"})

    assert res.status_code == 201
    assert res.json()["is_admin"] is True
    assert "minicam_session" in res.cookies
    assert led.stopped == 1
    assert client.get("/api/auth/setup").json()["setup_required"] is False
    again = client.post("/api/auth/setup", json={**ADMIN, "setup_code": "2468"})
    assert again.status_code == 409


def test_setup_code_not_in_logs_or_responses(
    client: TestClient, service: SetupClaimService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        texts = [
            client.post("/api/auth/setup/claim").text,
            client.get("/api/auth/setup").text,
            client.post("/api/auth/setup", json={**ADMIN, "setup_code": "1357"}).text,
            client.post("/api/auth/setup", json={**ADMIN, "setup_code": "2468"}).text,
        ]
    assert all("2468" not in text for text in texts)
    assert "2468" not in caplog.text


def test_login_page_asks_for_the_led_code(
    client: TestClient, service: SetupClaimService
) -> None:
    page = client.get("/login").text
    assert 'id="claim-button"' in page
    assert 'id="setup-code"' in page


def test_login_page_without_led_claim(client: TestClient) -> None:
    page = client.get("/login").text
    assert "Create admin account" in page
    assert 'id="setup-code"' not in page
