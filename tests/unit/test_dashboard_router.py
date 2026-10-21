"""Tests for the dashboard router: GET /, GET /login."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.dependencies import (
    get_authorization_service,
    get_db_connection,
    get_optional_current_user,
    get_user_repository,
    require_admin,
)
from app.models.config import AppConfig, CameraConfig
from app.models.domain import User
from app.routers import dashboard as dashboard_router

_USER = User(
    id=1,
    email="viewer@example.com",
    password_hash="hashed",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)
_ADMIN = User(
    id=2,
    email="admin@example.com",
    password_hash="hashed",
    is_admin=True,
    is_active=True,
    created_at="t",
    updated_at="t",
)


def _make_camera(name: str = "cam1") -> CameraConfig:
    return CameraConfig(name=name, host="192.168.1.10", password="secret")


def _make_client() -> TestClient:
    test_app = FastAPI()
    test_app.mount("/static", StaticFiles(directory="app/static"), name="static")
    test_app.include_router(dashboard_router.router)
    # Any repo built from an un-overridden factory must not touch the real
    # DB singleton — see the same pattern in test_auth_router.py.
    test_app.dependency_overrides[get_db_connection] = lambda: MagicMock()
    return TestClient(test_app)


@pytest.fixture()
def client() -> TestClient:
    return _make_client()


def _allow_all(*camera_names: str) -> AsyncMock:
    service = AsyncMock()
    service.list_viewable_camera_names.return_value = list(camera_names)
    return service


class TestHomeDashboardUnauthenticated:
    def test_redirects_to_login_when_no_session(self, client: TestClient) -> None:
        client.app.dependency_overrides[get_optional_current_user] = lambda: None
        res = client.get("/", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"


class TestHomeDashboardAuthenticated:
    def test_returns_200(self, client: TestClient) -> None:
        config = AppConfig(cameras=[_make_camera()])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: _allow_all(
            "cam1"
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert res.status_code == 200

    def test_returns_html(self, client: TestClient) -> None:
        config = AppConfig(cameras=[_make_camera()])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: _allow_all(
            "cam1"
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert "text/html" in res.headers["content-type"]

    def test_no_cameras_returns_200(self, client: TestClient) -> None:
        config = AppConfig(cameras=[])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: (
            _allow_all()
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert res.status_code == 200

    def test_first_camera_is_active(self, client: TestClient) -> None:
        config = AppConfig(cameras=[_make_camera("entrada"), _make_camera("patio")])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: _allow_all(
            "entrada", "patio"
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert "entrada" in res.text

    def test_empty_cameras_active_is_blank(self, client: TestClient) -> None:
        config = AppConfig(cameras=[])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: (
            _allow_all()
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert res.status_code == 200

    def test_filters_out_cameras_without_view_permission(
        self, client: TestClient
    ) -> None:
        config = AppConfig(cameras=[_make_camera("entrada"), _make_camera("patio")])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: _allow_all(
            "entrada"
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert "entrada" in res.text
        assert "patio" not in res.text

    def test_admin_link_hidden_for_regular_user(self, client: TestClient) -> None:
        config = AppConfig(cameras=[])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _USER
        client.app.dependency_overrides[get_authorization_service] = lambda: (
            _allow_all()
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert 'href="/admin"' not in res.text

    def test_admin_link_shown_for_admin_user(self, client: TestClient) -> None:
        config = AppConfig(cameras=[])
        client.app.dependency_overrides[get_optional_current_user] = lambda: _ADMIN
        client.app.dependency_overrides[get_authorization_service] = lambda: (
            _allow_all()
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert 'href="/admin"' in res.text


def _users(count: int) -> AsyncMock:
    repository = AsyncMock()
    repository.count_users.return_value = count
    return repository


class TestLoginPage:
    def test_returns_200(self, client: TestClient) -> None:
        client.app.dependency_overrides[get_user_repository] = lambda: _users(1)
        res = client.get("/login")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    def test_shows_only_login_form_once_a_user_exists(self, client: TestClient) -> None:
        client.app.dependency_overrides[get_user_repository] = lambda: _users(1)
        res = client.get("/login")
        assert 'id="login-form"' in res.text
        assert 'id="setup-form"' not in res.text

    def test_shows_first_admin_form_when_no_user_exists(
        self, client: TestClient
    ) -> None:
        client.app.dependency_overrides[get_user_repository] = lambda: _users(0)
        res = client.get("/login")
        assert 'id="setup-form"' in res.text
        assert 'id="login-form"' not in res.text
        assert "Create admin account" in res.text


class TestAdminPage:
    def test_requires_admin(self, client: TestClient) -> None:
        res = client.get("/admin")
        assert res.status_code == 401  # require_admin -> get_current_user -> 401

    def test_returns_200_for_admin(self, client: TestClient) -> None:
        client.app.dependency_overrides[require_admin] = lambda: _ADMIN
        res = client.get("/admin")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]


class TestAiDetectionPage:
    def test_requires_admin(self, client: TestClient) -> None:
        res = client.get("/ai-detection")
        assert res.status_code == 401  # require_admin -> get_current_user -> 401

    def test_returns_200_for_admin(self, client: TestClient) -> None:
        client.app.dependency_overrides[require_admin] = lambda: _ADMIN
        res = client.get("/ai-detection")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
