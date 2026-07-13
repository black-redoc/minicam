"""End-to-end regression test for the exact bug a real operator hit:
running the CLI (which calls init_db()) and then starting the server
(whose lifespan also calls init_db()) against the same database file used
to raise `sqlite3.OperationalError: table users already exists`.

Drives main.app's real lifespan twice against the same file path, the way
two separate process launches (CLI, then `uvicorn main:app`) would.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from app.db.connection import get_connection
from app.models.config import AppConfig
from app.repositories.user_repository import SQLiteUserRepository
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher


async def test_restarting_the_server_against_an_already_initialized_db_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINICAM_JWT_SECRET", "restart-test-secret")
    monkeypatch.setenv("MINICAM_DB_PATH", str(tmp_path / "restart.db"))
    get_security_settings.cache_clear()

    try:
        with patch.object(main, "get_config", return_value=AppConfig()):
            # First "process launch": the CLI creates the schema + an admin.
            with TestClient(main.app):
                user_repository = SQLiteUserRepository(get_connection())
                await user_repository.create(
                    "admin@example.com", PasswordHasher().hash("pw"), is_admin=True
                )

            # Second "process launch": the server starts against the same
            # file. Must not raise "table already exists".
            with TestClient(main.app) as client:
                res = client.post(
                    "/api/auth/login",
                    json={"email": "admin@example.com", "password": "pw"},
                )
                assert res.status_code == 200
    finally:
        get_security_settings.cache_clear()
