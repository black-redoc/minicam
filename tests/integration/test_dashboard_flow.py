"""Integration: dashboard renders only cameras the caller has can_view on,
backed by a real SQLite database.
"""

from __future__ import annotations

from unittest.mock import patch

import aiosqlite
from fastapi.testclient import TestClient

from app.models.config import AppConfig, CameraConfig
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import dashboard as dashboard_router
from app.services.auth import PasswordHasher


async def test_redirects_to_login_when_not_authenticated(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


async def test_dashboard_shows_only_permitted_cameras(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create(
        "viewer@example.com", PasswordHasher().hash("pw"), is_admin=False
    )
    perm_repo = SQLitePermissionRepository(db_connection)
    await perm_repo.upsert(user.id, "entrada", can_view=True, can_use_ptz=False)
    # "patio" is configured but has no permission row — deny-by-default.

    client.post(
        "/api/auth/login", json={"email": "viewer@example.com", "password": "pw"}
    )

    config = AppConfig(
        cameras=[
            CameraConfig(name="entrada", host="192.168.1.10"),
            CameraConfig(name="patio", host="192.168.1.11"),
        ]
    )
    with patch.object(dashboard_router, "get_config", return_value=config):
        res = client.get("/")

    assert res.status_code == 200
    assert "entrada" in res.text
    assert "patio" not in res.text


async def test_dashboard_renders_with_no_permitted_cameras(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    await user_repo.create(
        "novisibility@example.com", PasswordHasher().hash("pw"), is_admin=False
    )
    client.post(
        "/api/auth/login",
        json={"email": "novisibility@example.com", "password": "pw"},
    )

    config = AppConfig(cameras=[CameraConfig(name="entrada", host="192.168.1.10")])
    with patch.object(dashboard_router, "get_config", return_value=config):
        res = client.get("/")

    assert res.status_code == 200
    assert "entrada" not in res.text


async def test_admin_link_appears_for_admin_only(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    await user_repo.create(
        "regular3@example.com", PasswordHasher().hash("pw"), is_admin=False
    )
    await user_repo.create(
        "admin3@example.com", PasswordHasher().hash("pw"), is_admin=True
    )

    with patch.object(dashboard_router, "get_config", return_value=AppConfig()):
        client.post(
            "/api/auth/login", json={"email": "regular3@example.com", "password": "pw"}
        )
        regular_res = client.get("/")
        assert 'href="/admin"' not in regular_res.text

        client.post("/api/auth/logout")
        client.post(
            "/api/auth/login", json={"email": "admin3@example.com", "password": "pw"}
        )
        admin_res = client.get("/")
        assert 'href="/admin"' in admin_res.text


async def test_admin_page_requires_admin(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    await user_repo.create(
        "regular4@example.com", PasswordHasher().hash("pw"), is_admin=False
    )
    client.post(
        "/api/auth/login", json={"email": "regular4@example.com", "password": "pw"}
    )

    res = client.get("/admin")
    assert res.status_code == 403


async def test_admin_page_renders_for_admin(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    await user_repo.create(
        "admin4@example.com", PasswordHasher().hash("pw"), is_admin=True
    )
    client.post(
        "/api/auth/login", json={"email": "admin4@example.com", "password": "pw"}
    )

    res = client.get("/admin")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
