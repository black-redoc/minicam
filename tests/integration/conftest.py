"""Shared fixtures for integration tests: a fully wired FastAPI app backed by
a real in-memory SQLite database instead of mocked repositories/services.

get_network_zone defaults to LAN so most flows don't have to think about the
WAN gate — TestClient's synthetic client IP ("testclient") is not parseable,
so classify_network's fail-safe would otherwise classify every request as
WAN. Tests that specifically exercise the WAN gate override it to WAN
explicitly (see test_wan_access_gate_flow.py), following the plan's guidance
to override get_network_zone in tests rather than faking IPs.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.db.connection import apply_migrations
from app.dependencies import get_db_connection, get_network_zone
from app.routers import auth as auth_router
from app.routers import dashboard as dashboard_router
from app.routers import ptz as ptz_router
from app.routers import setup as setup_router
from app.routers import stream as stream_router
from app.security_settings import get_security_settings
from app.services.network import NetworkZone


@pytest.fixture(autouse=True)
def isolate_security_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINICAM_JWT_SECRET", "integration-test-secret")
    monkeypatch.setenv("MINICAM_STREAM_TOKEN_TTL_SECONDS", "30")
    get_security_settings.cache_clear()
    yield
    get_security_settings.cache_clear()


@pytest.fixture
async def db_connection() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await apply_migrations(conn)
    yield conn
    await conn.close()


@pytest.fixture
def app(db_connection: aiosqlite.Connection) -> FastAPI:
    test_app = FastAPI()
    test_app.mount("/static", StaticFiles(directory="app/static"), name="static")
    test_app.include_router(auth_router.router)
    test_app.include_router(dashboard_router.router)
    test_app.include_router(stream_router.router)
    test_app.include_router(ptz_router.router)
    test_app.include_router(setup_router.router)
    test_app.dependency_overrides[get_db_connection] = lambda: db_connection
    test_app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)
