"""Tests for the setup router: CRUD /api/cameras and /api/setup/discover."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.dependencies import require_admin
from app.models.config import AppConfig, CameraConfig, DiscoveredCamera
from app.models.domain import User
from app.routers import setup as setup_router
from app.services.camera import RTSPCamera

_ADMIN = User(
    id=1,
    email="admin@example.com",
    password_hash="hashed",
    is_admin=True,
    is_active=True,
    created_at="t",
    updated_at="t",
)


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
    test_app.dependency_overrides[require_admin] = lambda: _ADMIN
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
        with patch.object(
            setup_router, "add_camera", side_effect=ValueError("duplicate")
        ):
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

    def test_returns_404_when_no_password_and_camera_missing(
        self, client: TestClient
    ) -> None:
        empty_config = _make_app_config()
        body = {**self._body, "name": "ghost", "password": ""}
        with patch.object(setup_router, "get_config", return_value=empty_config):
            res = client.put("/api/cameras/ghost", json=body)
        assert res.status_code == 404

    def test_renaming_camera_drops_old_buffer(
        self, client: TestClient, mock_buffer: MagicMock
    ) -> None:
        renamed_body = {**self._body, "name": "cam1_renamed"}
        with (
            patch.object(setup_router, "update_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
        ):
            client.put("/api/cameras/cam1", json=renamed_body)
        assert "cam1" not in state_module.frame_buffers

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


class TestStartCameraAndPtz:
    async def test_creates_new_buffer_when_none_exists(self) -> None:
        cam_config = _make_camera("cam_new", "192.168.1.20")
        assert "cam_new" not in state_module.frame_buffers
        try:
            with patch.object(setup_router.RTSPCamera, "start"):
                setup_router._start_camera_and_ptz(cam_config)

            buf = state_module.frame_buffers["cam_new"]
            assert isinstance(buf, DoubleFrameBuffer)
            assert isinstance(state_module.cameras["cam_new"], RTSPCamera)
            assert "cam_new" in state_module.ptz_services
        finally:
            state_module.cameras.pop("cam_new", None)
            state_module.ptz_services.pop("cam_new", None)
            state_module.frame_buffers.pop("cam_new", None)

    async def test_reuses_existing_buffer(self) -> None:
        cam_config = _make_camera("cam_existing", "192.168.1.21")
        existing_buf = DoubleFrameBuffer()
        state_module.frame_buffers["cam_existing"] = existing_buf
        try:
            with patch.object(setup_router.RTSPCamera, "start"):
                setup_router._start_camera_and_ptz(cam_config)

            assert state_module.frame_buffers["cam_existing"] is existing_buf
        finally:
            state_module.cameras.pop("cam_existing", None)
            state_module.ptz_services.pop("cam_existing", None)
            state_module.frame_buffers.pop("cam_existing", None)


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
