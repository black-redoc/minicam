"""Fixtures for true end-to-end tests: boot the real FastAPI app (main.app,
its real lifespan, its real router registration) against a throwaway
file-backed SQLite database — not `:memory:`, not dependency_overrides.

This is the level a manual `curl`/`python -c` smoke test operates at, kept
as an automated regression instead of a one-off shell command. It exists
specifically to catch the class of bug that unit/integration tests miss
because they build their own test app and inject mocked or in-memory
collaborators: e.g. app/db/connection.apply_migrations() failing on a
database file that a previous process (the CLI) already initialized.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from app.dependencies import get_network_zone
from app.models.config import AppConfig
from app.security_settings import get_security_settings
from app.services.network import NetworkZone


@pytest.fixture
def e2e_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient]:
    """A TestClient wrapping the real main.app, with camera startup skipped.

    Skipping cameras is deliberate: this suite verifies the auth/admin
    flow, not RTSP connectivity, and starting real RTSPCamera threads
    against config.toml's hosts would make the test slow, flaky, and
    dependent on hardware that CI doesn't have.

    get_network_zone is overridden to LAN for the same reason
    tests/integration/conftest.py does it: TestClient's synthetic client
    host ("testclient") isn't a parseable IP, so classify_network's
    fail-safe would otherwise classify every request as WAN. A real
    browser on the operator's actual LAN (e.g. 192.168.x.x) is classified
    LAN correctly — this override only compensates for the test client,
    it does not change production behavior.
    """
    monkeypatch.setenv("MINICAM_JWT_SECRET", "e2e-test-secret")
    monkeypatch.setenv("MINICAM_DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setenv("MINICAM_STREAM_TOKEN_TTL_SECONDS", "30")
    get_security_settings.cache_clear()

    main.app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
    try:
        with patch.object(main, "get_config", return_value=AppConfig()):
            with TestClient(main.app) as client:
                yield client
    finally:
        main.app.dependency_overrides.pop(get_network_zone, None)
        get_security_settings.cache_clear()
