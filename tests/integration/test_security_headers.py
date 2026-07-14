"""Integration: SecurityHeadersMiddleware behaves differently on LAN vs
WAN — HSTS must never appear for LAN traffic, matching the plain-HTTP LAN
threat model documented in app/routers/auth.py.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.security_headers import SecurityHeadersMiddleware
from app.security_settings import get_security_settings

_TRUSTED_PROXY_IP = "203.0.113.9"


@pytest.fixture(autouse=True)
def configure_security_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("MINICAM_JWT_SECRET", "test-secret")
    monkeypatch.setenv("MINICAM_TRUSTED_PROXIES", _TRUSTED_PROXY_IP)
    get_security_settings.cache_clear()
    yield
    get_security_settings.cache_clear()


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)

    @test_app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    return test_app


class TestSecurityHeaders:
    def test_lan_request_gets_baseline_headers_without_hsts(self, app: FastAPI) -> None:
        client = TestClient(app, client=("192.168.1.50", 12345))

        res = client.get("/ping")

        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["referrer-policy"] == "no-referrer"
        assert "self" in res.headers["content-security-policy"]
        assert "strict-transport-security" not in res.headers

    def test_wan_request_via_trusted_proxy_gets_hsts(self, app: FastAPI) -> None:
        client = TestClient(app, client=(_TRUSTED_PROXY_IP, 12345))

        res = client.get("/ping")

        assert "strict-transport-security" in res.headers

    def test_wan_request_from_a_public_ip_gets_hsts(self, app: FastAPI) -> None:
        client = TestClient(app, client=("8.8.8.8", 12345))

        res = client.get("/ping")

        assert "strict-transport-security" in res.headers
