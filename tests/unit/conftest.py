"""Unit-test isolation: prevent get_config() from reading the real config.toml."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import aiosqlite
import pytest

from app.db.connection import apply_migrations
from app.models.config import AppConfig
from app.security_settings import get_security_settings


@pytest.fixture(autouse=True)
def isolate_config() -> pytest.FixtureFunction:
    """Prevent get_config() from reading the real config.toml during unit tests."""
    with patch("app.settings.get_config", return_value=AppConfig()):
        yield


@pytest.fixture(autouse=True)
def isolate_security_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a stable MINICAM_JWT_SECRET so get_security_settings() never
    hits the "not configured" RuntimeError during unit tests.
    """
    monkeypatch.setenv("MINICAM_JWT_SECRET", "unit-test-secret")
    get_security_settings.cache_clear()
    yield
    get_security_settings.cache_clear()


@pytest.fixture
async def db_connection() -> AsyncGenerator[aiosqlite.Connection]:
    """A fresh in-memory SQLite connection with migrations applied."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await apply_migrations(conn)
    yield conn
    await conn.close()
