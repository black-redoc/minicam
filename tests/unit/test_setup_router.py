"""Tests for the setup router: CRUD /api/cameras and /api/setup/discover."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import tomli_w
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

import app.state as state_module
from app.models.config import AppConfig, CameraConfig, DiscoveredCamera
from app.routers import setup as setup_router
from app.settings import get_config


def _make_camera(name: str = "cam1", host: str = "192.168.1.10") -> CameraConfig:
    return CameraConfig(name=name, host=host, password="secret")


def _make_app_config(*cameras: CameraConfig) -> AppConfig:
    return AppConfig(cameras=list(cameras))


@pytest.fixture()
def mock_camera() -> MagicMock:
    cam = MagicMock()
    cam.is_connected.return_value = True
    return cam


@pytest.fixture()
def mock_ptz() -> MagicMock:
    ptz = MagicMock()
    ptz.is_connected.return_value = True
    return ptz


@pytest.fixture()
def mock_buffer() -> MagicMock:
    buf = MagicMock()
    return buf


@pytest.fixture()
def client(
    monkeypatch: pytest.MonkeyPatch,
    mock_camera: MagicMock,
    mock_ptz: MagicMock,
    mock_buffer: MagicMock,
) -> TestClient:
    state_module.cameras["cam1"] = mock_camera
    state_module.ptz_services["cam1"] = mock_ptz
    state_module.frame_buffers["cam1"] = mock_buffer
    test_app = FastAPI()
    test_app.include_router(setup_router.router)
    yield TestClient(test_app)
    state_module.cameras.clear()
    state_module.ptz_services.clear()
    state_module.frame_buffers.clear()


class TestListCameras:
    def test_returns_200(self, client: TestClient) -> None:
        config = _make_app_config(_make_camera())
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/cameras")
        assert res.status_code == 200

    def test_returns_list(self, client: TestClient) -> None:
        config = _make_app_config(_make_camera())
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/cameras")
        assert isinstance(res.json(), list)
        assert len(res.json()) == 1

    def test_passwords_are_redacted(self, client: TestClient) -> None:
        config = _make_app_config(_make_camera())
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/cameras")
        assert res.json()[0]["password"] == ""


class TestGetCamera:
    def test_returns_200_when_exists(self, client: TestClient) -> None:
        config = _make_app_config(_make_camera())
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/cameras/cam1")
        assert res.status_code == 200

    def test_returns_404_when_missing(self, client: TestClient) -> None:
        config = _make_app_config()
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/cameras/ghost")
        assert res.status_code == 404

    def test_password_is_redacted(self, client: TestClient) -> None:
        config = _make_app_config(_make_camera())
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/cameras/cam1")
        assert res.json()["password"] == ""


class TestCreateCamera:
    _body: dict[str, object] = {
        "name": "cam2",
        "host": "10.0.0.2",
        "rtsp_port": 554,
        "onvif_port": 80,
        "user": "admin",
        "password": "pass",
        "channel": 1,
        "stream": "main",
    }

    def test_returns_201(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "add_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            res = client.post("/api/cameras", json=self._body)
        assert res.status_code == 201

    def test_password_is_redacted_in_response(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "add_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            res = client.post("/api/cameras", json=self._body)
        assert res.json()["password"] == ""

    def test_calls_add_camera(self, client: TestClient) -> None:
        mock_add = MagicMock()
        with (
            patch.object(setup_router, "add_camera", mock_add),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            client.post("/api/cameras", json=self._body)
        mock_add.assert_called_once()

    def test_returns_409_on_duplicate(self, client: TestClient) -> None:
        with patch.object(setup_router, "add_camera", side_effect=ValueError("duplicate")):
            res = client.post("/api/cameras", json=self._body)
        assert res.status_code == 409


class TestEditCamera:
    _body: dict[str, object] = {
        "name": "cam1",
        "host": "10.0.0.99",
        "rtsp_port": 554,
        "onvif_port": 80,
        "user": "admin",
        "password": "newpass",
        "channel": 1,
        "stream": "main",
    }

    def test_returns_200(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "update_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            res = client.put("/api/cameras/cam1", json=self._body)
        assert res.status_code == 200

    def test_returns_404_when_camera_missing(self, client: TestClient) -> None:
        with patch.object(setup_router, "update_camera", side_effect=KeyError("ghost")):
            res = client.put("/api/cameras/ghost", json=self._body)
        assert res.status_code == 404

    def test_stops_existing_services(
        self, client: TestClient, mock_camera: MagicMock, mock_ptz: MagicMock
    ) -> None:
        with (
            patch.object(setup_router, "update_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            client.put("/api/cameras/cam1", json=self._body)
        mock_camera.stop.assert_called_once()
        mock_ptz.disconnect.assert_called_once()

    def test_empty_password_preserves_existing(self, client: TestClient) -> None:
        existing_config = _make_app_config(_make_camera("cam1"))
        captured: list[CameraConfig] = []

        def _capture(name: str, cam: CameraConfig) -> None:
            captured.append(cam)

        body = {**self._body, "password": ""}
        with (
            patch.object(setup_router, "update_camera", side_effect=_capture),
            patch.object(setup_router, "get_config", return_value=existing_config),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            res = client.put("/api/cameras/cam1", json=body)

        assert res.status_code == 200
        assert captured[0].password == "secret"


class TestDeleteCamera:
    def test_returns_200(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "remove_camera"),
            patch.object(setup_router, "get_config"),
        ):
            res = client.delete("/api/cameras/cam1")
        assert res.status_code == 200

    def test_returns_ok_status(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "remove_camera"),
            patch.object(setup_router, "get_config"),
        ):
            res = client.delete("/api/cameras/cam1")
        assert res.json() == {"status": "ok"}

    def test_returns_404_when_camera_missing(self, client: TestClient) -> None:
        with patch.object(setup_router, "remove_camera", side_effect=KeyError("ghost")):
            res = client.delete("/api/cameras/ghost")
        assert res.status_code == 404

    def test_stops_services(
        self, client: TestClient, mock_camera: MagicMock, mock_ptz: MagicMock
    ) -> None:
        with (
            patch.object(setup_router, "remove_camera"),
            patch.object(setup_router, "get_config"),
        ):
            client.delete("/api/cameras/cam1")
        mock_camera.stop.assert_called_once()


class TestDiscover:
    def test_returns_200(self, client: TestClient) -> None:
        with patch("app.routers.setup.discover_onvif_cameras", return_value=[]):
            res = client.get("/api/setup/discover")
        assert res.status_code == 200

    def test_returns_empty_list_when_no_cameras(self, client: TestClient) -> None:
        with patch("app.routers.setup.discover_onvif_cameras", return_value=[]):
            res = client.get("/api/setup/discover")
        assert res.json() == []

    def test_returns_discovered_cameras(self, client: TestClient) -> None:
        cameras = [
            DiscoveredCamera(
                host="192.168.1.50",
                name="IPCamera",
                hardware="DS-2CD",
                location="Garage",
            )
        ]
        with patch("app.routers.setup.discover_onvif_cameras", return_value=cameras):
            res = client.get("/api/setup/discover")
        data = res.json()
        assert len(data) == 1
        assert data[0]["host"] == "192.168.1.50"
