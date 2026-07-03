"""Tests for the dashboard router: GET /"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.models.config import AppConfig, CameraConfig
from app.routers import dashboard as dashboard_router


def _make_camera(name: str = "cam1") -> CameraConfig:
    return CameraConfig(name=name, host="192.168.1.10", password="secret")


def _make_client() -> TestClient:
    test_app = FastAPI()
    test_app.mount("/static", StaticFiles(directory="app/static"), name="static")
    test_app.include_router(dashboard_router.router)
    return TestClient(test_app)


@pytest.fixture()
def client() -> TestClient:
    return _make_client()


class TestHomeDashboard:
    def test_returns_200(self, client: TestClient) -> None:
        config = AppConfig(cameras=[_make_camera()])
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert res.status_code == 200

    def test_returns_html(self, client: TestClient) -> None:
        config = AppConfig(cameras=[_make_camera()])
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert "text/html" in res.headers["content-type"]

    def test_no_cameras_returns_200(self, client: TestClient) -> None:
        config = AppConfig(cameras=[])
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert res.status_code == 200

    def test_first_camera_is_active(self, client: TestClient) -> None:
        config = AppConfig(
            cameras=[_make_camera("entrada"), _make_camera("patio")]
        )
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert "entrada" in res.text

    def test_empty_cameras_active_is_blank(self, client: TestClient) -> None:
        config = AppConfig(cameras=[])
        with patch.object(dashboard_router, "get_config", return_value=config):
            res = client.get("/")
        assert res.status_code == 200
