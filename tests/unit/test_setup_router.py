"""Tests for the setup router: CRUD /api/cameras and /api/setup/discover."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.state as state_module
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import require_admin
from app.models.config import (
    AppConfig,
    CameraConfig,
    CameraConfigRequest,
    DetectionConfig,
    DiscoveredCamera,
)
from app.models.domain import User
from app.routers import setup as setup_router
from app.services.camera import RTSPCamera
from app.services.detection.base import Detection

_ADMIN = User(
    id=1,
    email="admin@example.com",
    password_hash="hashed",
    is_admin=True,
    is_active=True,
    created_at="t",
    updated_at="t",
)


class _FakeDetectionBackend:
    """Backend double whose available_classes is fully controlled per test."""

    def __init__(self, available_classes: tuple[str, ...] = ("person", "car")) -> None:
        self._available_classes = available_classes

    @property
    def available_classes(self) -> tuple[str, ...]:
        return self._available_classes

    def detect(
        self, image: np.ndarray, allowed_classes: frozenset[str]
    ) -> list[Detection]:
        return []


class _FakeDetectionBuffer:
    """Buffer double that never reports a frame — enough to exercise a
    DetectionWorker's task lifecycle without a running camera."""

    def read(self) -> None:
        return None

    def shutdown(self) -> None:
        pass


async def _cleanup_detection(name: str) -> None:
    """Cancel and clear any worker task left behind by a test."""
    await setup_router._stop_detection(name)
    state_module.frame_buffers.pop(name, None)


def _make_camera(
    name: str = "cam1",
    host: str = "192.168.1.10",
    detection_classes: list[str] | None = None,
) -> CameraConfig:
    return CameraConfig(
        name=name,
        host=host,
        password="secret",
        detection_classes=detection_classes or [],
    )


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
            assert isinstance(buf, FrameBuffer)
            assert isinstance(state_module.cameras["cam_new"], RTSPCamera)
            assert "cam_new" in state_module.ptz_services
        finally:
            state_module.cameras.pop("cam_new", None)
            state_module.ptz_services.pop("cam_new", None)
            state_module.frame_buffers.pop("cam_new", None)

    async def test_reuses_existing_buffer(self) -> None:
        cam_config = _make_camera("cam_existing", "192.168.1.21")
        existing_buf = FrameBuffer()
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


class TestStartDetection:
    async def test_creates_worker_task_when_backend_available(self) -> None:
        cam_config = _make_camera("cam_det_start", detection_classes=["person"])
        state_module.frame_buffers["cam_det_start"] = _FakeDetectionBuffer()
        backend = _FakeDetectionBackend()
        try:
            with patch.object(
                setup_router, "get_detection_backend", return_value=backend
            ):
                setup_router._start_detection(cam_config)

            task = state_module.detection_workers.get("cam_det_start")
            assert isinstance(task, asyncio.Task)
            assert not task.done()
        finally:
            await _cleanup_detection("cam_det_start")

    async def test_no_task_when_detection_classes_empty(self) -> None:
        cam_config = _make_camera("cam_det_empty")
        with patch.object(setup_router, "get_detection_backend") as mock_get_backend:
            setup_router._start_detection(cam_config)

        mock_get_backend.assert_not_called()
        assert "cam_det_empty" not in state_module.detection_workers

    async def test_logs_warning_and_skips_when_no_backend(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cam_config = _make_camera("cam_det_no_backend", detection_classes=["person"])
        with (
            patch.object(setup_router, "get_detection_backend", return_value=None),
            caplog.at_level("WARNING"),
        ):
            setup_router._start_detection(cam_config)

        assert "cam_det_no_backend" not in state_module.detection_workers
        assert any(
            "no detection backend is active" in record.message
            for record in caplog.records
        )


class TestStopDetection:
    async def test_cancels_and_clears_running_task(self) -> None:
        async def _spin_forever() -> None:
            await asyncio.sleep(1000)

        task = asyncio.create_task(_spin_forever())
        state_module.detection_workers["cam_det_stop"] = task
        state_module.detection_results["cam_det_stop"] = None

        await setup_router._stop_detection("cam_det_stop")

        assert "cam_det_stop" not in state_module.detection_workers
        assert "cam_det_stop" not in state_module.detection_results
        assert task.done()
        assert task.cancelled()

    async def test_is_noop_when_nothing_running(self) -> None:
        assert "cam_det_ghost" not in state_module.detection_workers
        await setup_router._stop_detection("cam_det_ghost")  # must not raise
        assert "cam_det_ghost" not in state_module.detection_workers


class TestCreateCameraDetectionHook:
    _body: dict[str, object] = {
        "name": "cam_det_create_hook",
        "host": "10.0.0.11",
        "rtsp_port": 554,
        "onvif_port": 80,
        "user": "admin",
        "password": "pass",
        "channel": 1,
        "stream": "main",
        "detection_classes": ["person"],
    }

    def test_calls_start_detection(self, client: TestClient) -> None:
        mock_start_detection = MagicMock()
        with (
            patch.object(setup_router, "add_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
            patch.object(setup_router, "_start_detection", mock_start_detection),
            patch.object(
                setup_router,
                "get_detection_backend",
                return_value=_FakeDetectionBackend(),
            ),
        ):
            res = client.post("/api/cameras", json=self._body)

        assert res.status_code == 201
        mock_start_detection.assert_called_once()

    def test_response_includes_detection_classes(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "add_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
            patch.object(setup_router, "_start_detection"),
            patch.object(
                setup_router,
                "get_detection_backend",
                return_value=_FakeDetectionBackend(),
            ),
        ):
            res = client.post("/api/cameras", json=self._body)

        assert res.json()["detection_classes"] == ["person"]


class TestEditCameraDetectionHook:
    _body: dict[str, object] = {
        "name": "cam1",
        "host": "10.0.0.99",
        "rtsp_port": 554,
        "onvif_port": 80,
        "user": "admin",
        "password": "newpass",
        "channel": 1,
        "stream": "main",
        "detection_classes": ["person"],
    }

    def test_calls_stop_and_start_detection(self, client: TestClient) -> None:
        mock_stop_detection = AsyncMock()
        mock_start_detection = MagicMock()
        with (
            patch.object(setup_router, "update_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
            patch.object(setup_router, "_stop_detection", mock_stop_detection),
            patch.object(setup_router, "_start_detection", mock_start_detection),
            patch.object(
                setup_router,
                "get_detection_backend",
                return_value=_FakeDetectionBackend(),
            ),
        ):
            res = client.put("/api/cameras/cam1", json=self._body)

        assert res.status_code == 200
        mock_stop_detection.assert_awaited_once_with("cam1")
        mock_start_detection.assert_called_once()


class TestDeleteCameraDetectionHook:
    def test_calls_stop_detection(self, client: TestClient) -> None:
        mock_stop_detection = AsyncMock()
        with (
            patch.object(setup_router, "remove_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_stop_detection", mock_stop_detection),
        ):
            res = client.delete("/api/cameras/cam1")

        assert res.status_code == 200
        mock_stop_detection.assert_awaited_once_with("cam1")


class TestCameraNotificationsField:
    def test_create_returns_notification_mode(self, client: TestClient) -> None:
        body = {
            "name": "cam_notify",
            "host": "10.0.0.20",
            "password": "pass",
            "notifications": "on",
        }
        with (
            patch.object(setup_router, "add_camera") as mock_add,
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
            patch.object(setup_router, "_start_detection"),
            patch.object(setup_router, "get_detection_backend", return_value=None),
        ):
            res = client.post("/api/cameras", json=body)

        assert res.status_code == 201
        assert res.json()["notifications"] == "on"
        assert mock_add.call_args.args[0].notifications == "on"

    def test_rejects_unknown_notification_mode(self, client: TestClient) -> None:
        body = {"name": "c", "host": "h", "notifications": "always"}
        assert client.post("/api/cameras", json=body).status_code == 422

    def test_delete_forgets_notification_state(self, client: TestClient) -> None:
        with (
            patch.object(setup_router, "remove_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_stop_detection", AsyncMock()),
            patch.object(setup_router.notification_runtime, "notifier") as notifier,
        ):
            res = client.delete("/api/cameras/cam1")

        assert res.status_code == 200
        notifier.forget.assert_called_once_with("cam1")

    def test_rename_forgets_old_notification_state(self, client: TestClient) -> None:
        old = _make_camera("cam1")
        body = {"name": "cam1-renamed", "host": "10.0.0.21", "password": "pass"}
        with (
            patch.object(setup_router, "update_camera"),
            patch.object(
                setup_router, "get_config", return_value=_make_app_config(old)
            ),
            patch.object(setup_router, "_stop_camera", AsyncMock()),
            patch.object(setup_router, "_stop_ptz", AsyncMock()),
            patch.object(setup_router, "_stop_detection", AsyncMock()),
            patch.object(setup_router, "_start_camera_and_ptz"),
            patch.object(setup_router, "_start_detection"),
            patch.object(setup_router, "get_detection_backend", return_value=None),
            patch.object(setup_router.notification_runtime, "notifier") as notifier,
        ):
            res = client.put("/api/cameras/cam1", json=body)

        assert res.status_code == 200
        notifier.forget.assert_called_once_with("cam1")


class TestDetectionClassValidation:
    def test_create_camera_returns_400_for_unknown_class(
        self, client: TestClient
    ) -> None:
        backend = _FakeDetectionBackend(available_classes=("car",))
        body = {
            "name": "cam_bad_class",
            "host": "10.0.0.12",
            "password": "pass",
            "detection_classes": ["person"],
        }
        with patch.object(setup_router, "get_detection_backend", return_value=backend):
            res = client.post("/api/cameras", json=body)

        assert res.status_code == 400
        assert "person" in res.json()["detail"]

    def test_create_camera_allows_unknown_class_without_backend(
        self, client: TestClient
    ) -> None:
        body = {
            "name": "cam_no_backend",
            "host": "10.0.0.13",
            "password": "pass",
            "detection_classes": ["person"],
        }
        with (
            patch.object(setup_router, "add_camera"),
            patch.object(setup_router, "get_config"),
            patch.object(setup_router, "_start_camera_and_ptz"),
            patch.object(setup_router, "_start_detection"),
            patch.object(setup_router, "get_detection_backend", return_value=None),
        ):
            res = client.post("/api/cameras", json=body)

        assert res.status_code == 201

    def test_edit_camera_returns_400_for_unknown_class(
        self, client: TestClient
    ) -> None:
        backend = _FakeDetectionBackend(available_classes=("car",))
        body = {
            "name": "cam1",
            "host": "10.0.0.99",
            "password": "newpass",
            "detection_classes": ["person"],
        }
        with patch.object(setup_router, "get_detection_backend", return_value=backend):
            res = client.put("/api/cameras/cam1", json=body)

        assert res.status_code == 400
        assert "person" in res.json()["detail"]


class TestRenameCameraWithDetection:
    async def test_moves_worker_to_new_name(self) -> None:
        old_name = "cam_rename_det_src"
        new_name = "cam_rename_det_dst"
        state_module.frame_buffers[old_name] = _FakeDetectionBuffer()
        state_module.frame_buffers[new_name] = _FakeDetectionBuffer()
        backend = _FakeDetectionBackend()
        body = CameraConfigRequest(
            name=new_name,
            host="10.0.0.50",
            password="pass",
            detection_classes=["person"],
        )

        try:
            with (
                patch.object(setup_router, "update_camera"),
                patch.object(setup_router, "get_config"),
                patch.object(setup_router, "_start_camera_and_ptz"),
                patch.object(
                    setup_router, "get_detection_backend", return_value=backend
                ),
            ):
                old_cam_config = _make_camera(old_name, detection_classes=["person"])
                setup_router._start_detection(old_cam_config)
                old_task = state_module.detection_workers[old_name]

                await setup_router.edit_camera(old_name, body)

            assert old_name not in state_module.detection_workers
            assert old_name not in state_module.detection_results
            assert old_task.cancelled()
            assert new_name in state_module.detection_workers
        finally:
            await _cleanup_detection(old_name)
            await _cleanup_detection(new_name)


class TestEditCameraClearsDetection:
    async def test_stops_worker_when_classes_cleared(self) -> None:
        name = "cam_clear_det"
        state_module.frame_buffers[name] = _FakeDetectionBuffer()
        backend = _FakeDetectionBackend()
        old_cam_config = _make_camera(name, detection_classes=["person"])

        with patch.object(setup_router, "get_detection_backend", return_value=backend):
            setup_router._start_detection(old_cam_config)
        old_task = state_module.detection_workers[name]

        body = CameraConfigRequest(name=name, host="10.0.0.60", password="pass")

        try:
            with (
                patch.object(setup_router, "update_camera"),
                patch.object(setup_router, "get_config"),
                patch.object(setup_router, "_start_camera_and_ptz"),
                patch.object(
                    setup_router, "get_detection_backend", return_value=backend
                ),
            ):
                await setup_router.edit_camera(name, body)

            assert name not in state_module.detection_workers
            assert name not in state_module.detection_results
            assert old_task.cancelled()
        finally:
            await _cleanup_detection(name)


class TestDeleteCameraWithDetection:
    async def test_stops_worker(self) -> None:
        name = "cam_delete_det"
        state_module.frame_buffers[name] = _FakeDetectionBuffer()
        backend = _FakeDetectionBackend()
        cam_config = _make_camera(name, detection_classes=["person"])

        with patch.object(setup_router, "get_detection_backend", return_value=backend):
            setup_router._start_detection(cam_config)
        task = state_module.detection_workers[name]

        try:
            with (
                patch.object(setup_router, "remove_camera"),
                patch.object(setup_router, "get_config"),
            ):
                await setup_router.delete_camera(name)

            assert name not in state_module.detection_workers
            assert name not in state_module.detection_results
            assert task.cancelled()
        finally:
            await _cleanup_detection(name)
            state_module.frame_buffers.pop(name, None)


class TestGetDetectionSettings:
    def test_returns_200(self, client: TestClient) -> None:
        config = AppConfig(detection=DetectionConfig())
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/detection/settings")
        assert res.status_code == 200

    def test_returns_current_detection_config(self, client: TestClient) -> None:
        config = AppConfig(
            detection=DetectionConfig(backend="onnx", model_path="/models/yolo.onnx")
        )
        with patch.object(setup_router, "get_config", return_value=config):
            res = client.get("/api/detection/settings")
        assert res.json()["backend"] == "onnx"
        assert res.json()["model_path"] == "/models/yolo.onnx"


class TestUpdateDetectionSettings:
    _valid_body: dict[str, object] = {
        "backend": "none",
        "model_path": "",
        "confidence_threshold": 0.5,
        "inference_fps": 3.0,
    }

    def test_returns_400_when_onnx_model_path_is_relative(
        self, client: TestClient
    ) -> None:
        body = {**self._valid_body, "backend": "onnx", "model_path": "relative.onnx"}
        res = client.put("/api/detection/settings", json=body)
        assert res.status_code == 400

    def test_returns_400_when_onnx_model_path_does_not_exist(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        body = {
            **self._valid_body,
            "backend": "onnx",
            "model_path": str(tmp_path / "missing.onnx"),
        }
        res = client.put("/api/detection/settings", json=body)
        assert res.status_code == 400

    def test_success_persists_and_clears_caches(self, client: TestClient) -> None:
        persisted = AppConfig(detection=DetectionConfig(backend="none"))
        mock_update = MagicMock(return_value=persisted)
        mock_get_config = MagicMock(return_value=AppConfig())
        mock_get_backend = MagicMock(return_value=None)
        with (
            patch.object(setup_router, "update_detection_config", mock_update),
            patch.object(setup_router, "get_config", mock_get_config),
            patch.object(setup_router, "get_detection_backend", mock_get_backend),
        ):
            res = client.put("/api/detection/settings", json=self._valid_body)

        assert res.status_code == 200
        mock_update.assert_called_once()
        mock_get_config.cache_clear.assert_called_once()
        mock_get_backend.cache_clear.assert_called_once()

    def test_success_with_valid_onnx_model_path(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"fake model")
        body = {**self._valid_body, "backend": "onnx", "model_path": str(model_path)}
        persisted = AppConfig(
            detection=DetectionConfig(backend="onnx", model_path=str(model_path))
        )
        with (
            patch.object(
                setup_router, "update_detection_config", return_value=persisted
            ),
            patch.object(setup_router, "get_config", return_value=AppConfig()),
            patch.object(setup_router, "get_detection_backend", MagicMock()),
        ):
            res = client.put("/api/detection/settings", json=body)

        assert res.status_code == 200
        assert res.json()["model_path"] == str(model_path)

    def test_restarts_active_detection_workers(self, client: TestClient) -> None:
        cam_with_detection = CameraConfig(
            name="cam_with_det",
            host="10.0.0.5",
            password="pass",
            detection_classes=["person"],
        )
        config_after_update = AppConfig(
            cameras=[cam_with_detection], detection=DetectionConfig()
        )
        mock_stop = AsyncMock()
        mock_start = MagicMock()
        with (
            patch.object(
                setup_router,
                "update_detection_config",
                return_value=config_after_update,
            ),
            patch.object(setup_router, "get_config", return_value=config_after_update),
            patch.object(setup_router, "get_detection_backend", MagicMock()),
            patch.object(setup_router, "_stop_detection", mock_stop),
            patch.object(setup_router, "_start_detection", mock_start),
        ):
            res = client.put("/api/detection/settings", json=self._valid_body)

        assert res.status_code == 200
        mock_stop.assert_awaited_once_with("cam_with_det")
        mock_start.assert_called_once_with(cam_with_detection)

    def test_does_not_restart_cameras_without_detection_classes(
        self, client: TestClient
    ) -> None:
        cam_without_detection = CameraConfig(
            name="cam_no_det", host="10.0.0.6", password="pass"
        )
        config_after_update = AppConfig(
            cameras=[cam_without_detection], detection=DetectionConfig()
        )
        mock_stop = AsyncMock()
        mock_start = MagicMock()
        with (
            patch.object(
                setup_router,
                "update_detection_config",
                return_value=config_after_update,
            ),
            patch.object(setup_router, "get_config", return_value=config_after_update),
            patch.object(setup_router, "get_detection_backend", MagicMock()),
            patch.object(setup_router, "_stop_detection", mock_stop),
            patch.object(setup_router, "_start_detection", mock_start),
        ):
            res = client.put("/api/detection/settings", json=self._valid_body)

        assert res.status_code == 200
        mock_stop.assert_not_awaited()
        mock_start.assert_not_called()


class TestUploadDetectionModel:
    def test_saves_file_with_sanitized_name(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        with patch.object(setup_router, "_MODELS_DIR", tmp_path):
            res = client.post(
                "/api/detection/model",
                files={
                    "file": (
                        "model.onnx",
                        b"fake onnx bytes",
                        "application/octet-stream",
                    )
                },
            )

        assert res.status_code == 200
        saved_path = tmp_path / "model.onnx"
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"fake onnx bytes"
        assert res.json()["model_path"] == str(saved_path)

    def test_rejects_wrong_extension(self, client: TestClient, tmp_path: Path) -> None:
        with patch.object(setup_router, "_MODELS_DIR", tmp_path):
            res = client.post(
                "/api/detection/model",
                files={"file": ("model.txt", b"not onnx", "text/plain")},
            )

        assert res.status_code == 400

    def test_rejects_oversized_upload(self, client: TestClient, tmp_path: Path) -> None:
        oversized = b"x" * 100
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router, "_MAX_MODEL_UPLOAD_BYTES", 10),
            patch.object(setup_router, "_MODEL_UPLOAD_CHUNK_BYTES", 10),
        ):
            res = client.post(
                "/api/detection/model",
                files={"file": ("model.onnx", oversized, "application/octet-stream")},
            )

        assert res.status_code == 400
        assert not (tmp_path / "model.onnx").exists()

    def test_sanitizes_path_traversal_filename(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        with patch.object(setup_router, "_MODELS_DIR", tmp_path):
            res = client.post(
                "/api/detection/model",
                files={
                    "file": (
                        "../../etc/evil.onnx",
                        b"fake onnx bytes",
                        "application/octet-stream",
                    )
                },
            )

        assert res.status_code == 200
        assert res.json()["model_path"] == str(tmp_path / "evil.onnx")
        assert (tmp_path / "evil.onnx").exists()
        assert not (tmp_path.parent / "etc").exists()


class _FakeFetchResponse:
    """Minimal async-iterable stand-in for an httpx streaming response."""

    def __init__(
        self,
        status_code: int,
        chunks: list[bytes],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCtx:
    def __init__(
        self, response: _FakeFetchResponse | None, error: Exception | None
    ) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self) -> _FakeFetchResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient, used as both the class and the instance.

    `response` answers every URL; `routes` answers per URL (for redirects).
    """

    def __init__(
        self,
        response: _FakeFetchResponse | None = None,
        error: Exception | None = None,
        routes: dict[str, _FakeFetchResponse] | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._routes = routes or {}
        self.called = False
        self.client_kwargs: dict[str, object] = {}
        self.requested_urls: list[str] = []

    def __call__(self, *args: object, **kwargs: object) -> _FakeAsyncClient:
        self.called = True
        self.client_kwargs = kwargs
        return self

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def stream(self, method: str, url: str) -> _FakeStreamCtx:
        self.requested_urls.append(url)
        return _FakeStreamCtx(self._routes.get(url, self._response), self._error)


_PUBLIC_IP = "93.184.215.14"


@pytest.fixture()
def resolve_host() -> Iterator[AsyncMock]:
    """Resolve every host to a public IP unless a test overrides it."""
    mock = AsyncMock(return_value=[_PUBLIC_IP])
    with patch.object(setup_router, "_resolve_host", mock):
        yield mock


@pytest.mark.usefixtures("resolve_host")
class TestFetchDetectionModel:
    def test_downloads_and_saves_file(self, client: TestClient, tmp_path: Path) -> None:
        chunks = [b"fake ", b"onnx bytes"]
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, chunks))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/models/model.onnx"},
            )

        assert res.status_code == 200
        saved_path = tmp_path / "model.onnx"
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"fake onnx bytes"
        assert res.json()["model_path"] == str(saved_path)

    def test_rejects_non_https_url(self, client: TestClient, tmp_path: Path) -> None:
        fake = _FakeAsyncClient()
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "http://example.com/model.onnx"},
            )

        assert res.status_code == 400
        assert fake.called is False

    def test_rejects_non_200_response(self, client: TestClient, tmp_path: Path) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(404, []))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 502
        assert not (tmp_path / "model.onnx").exists()

    def test_rejects_oversized_download(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"x" * 20]))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router, "_MAX_MODEL_UPLOAD_BYTES", 10),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 400
        assert not (tmp_path / "model.onnx").exists()

    def test_rejects_non_onnx_filename(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient()
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.txt"},
            )

        assert res.status_code == 400
        assert fake.called is False

    def test_rejects_url_without_filename(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient()
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/"},
            )

        assert res.status_code == 400
        assert fake.called is False

    def test_translates_network_error(self, client: TestClient, tmp_path: Path) -> None:
        fake = _FakeAsyncClient(error=httpx.ConnectError("boom"))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 502
        assert not (tmp_path / "model.onnx").exists()

    def test_sanitizes_path_traversal_filename(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"fake onnx bytes"]))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/../../etc/evil.onnx"},
            )

        assert res.status_code == 200
        assert res.json()["model_path"] == str(tmp_path / "evil.onnx")

    def test_does_not_let_httpx_follow_redirects(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"onnx"]))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert fake.client_kwargs["follow_redirects"] is False

    def test_follows_https_redirect_to_public_host(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(
            routes={
                "https://example.com/model.onnx": _FakeFetchResponse(
                    302, [], {"location": "https://cdn.example.org/m/model.onnx"}
                ),
                "https://cdn.example.org/m/model.onnx": _FakeFetchResponse(
                    200, [b"onnx bytes"]
                ),
            }
        )
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 200
        assert (tmp_path / "model.onnx").read_bytes() == b"onnx bytes"

    def test_resolves_relative_redirect_location(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(
            routes={
                "https://example.com/a/model.onnx": _FakeFetchResponse(
                    301, [], {"location": "/b/model.onnx"}
                ),
                "https://example.com/b/model.onnx": _FakeFetchResponse(200, [b"onnx"]),
            }
        )
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/a/model.onnx"},
            )

        assert res.status_code == 200
        assert fake.requested_urls[-1] == "https://example.com/b/model.onnx"

    def test_rejects_redirect_to_plain_http(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(
            routes={
                "https://example.com/model.onnx": _FakeFetchResponse(
                    302, [], {"location": "http://example.com/model.onnx"}
                ),
            }
        )
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 400
        assert fake.requested_urls == ["https://example.com/model.onnx"]
        assert not (tmp_path / "model.onnx").exists()

    def test_rejects_redirect_to_internal_host(
        self, client: TestClient, tmp_path: Path, resolve_host: AsyncMock
    ) -> None:
        resolve_host.side_effect = lambda host: (
            ["192.168.1.1"] if host == "router.lan" else [_PUBLIC_IP]
        )
        fake = _FakeAsyncClient(
            routes={
                "https://example.com/model.onnx": _FakeFetchResponse(
                    302, [], {"location": "https://router.lan/model.onnx"}
                ),
            }
        )
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 400
        assert fake.requested_urls == ["https://example.com/model.onnx"]

    @pytest.mark.parametrize(
        "address", ["127.0.0.1", "10.0.0.7", "169.254.169.254", "::1", "fe80::1%eth0"]
    )
    def test_rejects_url_resolving_to_non_public_address(
        self,
        client: TestClient,
        tmp_path: Path,
        resolve_host: AsyncMock,
        address: str,
    ) -> None:
        resolve_host.return_value = [_PUBLIC_IP, address]
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"onnx"]))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 400
        assert fake.requested_urls == []

    def test_unresolvable_host_returns_502(
        self, client: TestClient, tmp_path: Path, resolve_host: AsyncMock
    ) -> None:
        resolve_host.side_effect = OSError("no such host")
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"onnx"]))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 502

    def test_redirect_without_location_returns_502(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(302, []))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 502

    def test_too_many_redirects_returns_502(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        loop_response = _FakeFetchResponse(
            302, [], {"location": "https://example.com/model.onnx"}
        )
        fake = _FakeAsyncClient(response=loop_response)
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 502
        assert len(fake.requested_urls) == setup_router._MAX_MODEL_FETCH_REDIRECTS + 1

    def test_failed_download_keeps_existing_model_and_leaves_no_temp_file(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "model.onnx").write_bytes(b"previous model")
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"x" * 20]))
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(setup_router, "_MAX_MODEL_UPLOAD_BYTES", 10),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/model/fetch",
                json={"url": "https://example.com/model.onnx"},
            )

        assert res.status_code == 400
        assert (tmp_path / "model.onnx").read_bytes() == b"previous model"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["model.onnx"]


class TestResolveHost:
    async def test_returns_resolved_addresses(self) -> None:
        addresses = await setup_router._resolve_host("localhost")
        assert "127.0.0.1" in addresses or "::1" in addresses


class TestDetectionClassesEndpoint:
    def test_returns_classes_when_backend_available(self, client: TestClient) -> None:
        backend = _FakeDetectionBackend(available_classes=("person", "car"))
        with patch.object(setup_router, "get_detection_backend", return_value=backend):
            res = client.get("/api/detection/classes")

        assert res.status_code == 200
        assert sorted(res.json()["classes"]) == ["car", "person"]

    def test_returns_empty_list_without_backend(self, client: TestClient) -> None:
        with patch.object(setup_router, "get_detection_backend", return_value=None):
            res = client.get("/api/detection/classes")

        assert res.status_code == 200
        assert res.json() == {"classes": []}


_MODEL_BYTES = b"fake onnx model bytes"
_MODEL_SHA256 = hashlib.sha256(_MODEL_BYTES).hexdigest()
_ASSET_URL = "https://github.com/x/minicam-models/releases/download/v9/tiny-int8.onnx"


def _catalog_manifest(**asset_overrides: object) -> dict[str, object]:
    asset: dict[str, object] = {
        "name": "tiny-int8.onnx",
        "model": "tiny",
        "family": "YOLO11",
        "precision": "int8",
        "profile": "Raspberry Pi 5 (CPU)",
        "bytes": len(_MODEL_BYTES),
        "sha256": _MODEL_SHA256,
        "url": _ASSET_URL,
    }
    asset.update(asset_overrides)
    return {
        "schema_version": 1,
        "release": "v9",
        "contract": {
            "input": {"shape": [1, 3, 640, 640]},
            "output": {"shape": [1, 84, 8400]},
            "classes": "coco80",
            "nms": False,
        },
        "assets": [asset],
    }


@pytest.mark.usefixtures("resolve_host")
class TestModelCatalog:
    def test_lists_assets_with_local_state(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(
                setup_router, "_fetch_json", AsyncMock(return_value=_catalog_manifest())
            ),
            patch.object(setup_router, "get_config", return_value=AppConfig()),
        ):
            res = client.get("/api/detection/catalog")

        assert res.status_code == 200
        body = res.json()
        assert body["release"] == "v9"
        assert body["catalog_url"].endswith("manifest.json")
        assert body["assets"][0]["name"] == "tiny-int8.onnx"
        assert body["assets"][0]["installed"] is False
        assert body["assets"][0]["active"] is False

    def test_marks_installed_and_active_model(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        model_path = tmp_path / "tiny-int8.onnx"
        model_path.write_bytes(_MODEL_BYTES)
        config = AppConfig(
            detection=DetectionConfig(backend="onnx", model_path=str(model_path))
        )
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(
                setup_router, "_fetch_json", AsyncMock(return_value=_catalog_manifest())
            ),
            patch.object(setup_router, "get_config", return_value=config),
        ):
            asset = client.get("/api/detection/catalog").json()["assets"][0]

        assert asset["installed"] is True
        assert asset["active"] is True

    def test_size_mismatch_is_not_reported_as_installed(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "tiny-int8.onnx").write_bytes(b"truncated")
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(
                setup_router, "_fetch_json", AsyncMock(return_value=_catalog_manifest())
            ),
            patch.object(setup_router, "get_config", return_value=AppConfig()),
        ):
            asset = client.get("/api/detection/catalog").json()["assets"][0]

        assert asset["installed"] is False

    def test_invalid_manifest_returns_502(self, client: TestClient) -> None:
        bad = _catalog_manifest()
        bad["schema_version"] = 99
        with patch.object(setup_router, "_fetch_json", AsyncMock(return_value=bad)):
            res = client.get("/api/detection/catalog")

        assert res.status_code == 502
        assert "Invalid model catalog" in res.json()["detail"]

    def test_fetches_manifest_from_configured_url(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pinned = "https://example.com/releases/download/v1/manifest.json"
        monkeypatch.setenv("MINICAM_MODEL_CATALOG_URL", pinned)
        fetch = AsyncMock(return_value=_catalog_manifest())
        with (
            patch.object(setup_router, "_fetch_json", fetch),
            patch.object(setup_router, "get_config", return_value=AppConfig()),
        ):
            client.get("/api/detection/catalog")

        assert fetch.call_args.args[0] == pinned


@pytest.mark.usefixtures("resolve_host")
class TestInstallCatalogModel:
    def _post(
        self,
        client: TestClient,
        tmp_path: Path,
        fake: _FakeAsyncClient,
        apply: AsyncMock,
        manifest: dict[str, object] | None = None,
        **body: object,
    ) -> httpx.Response:
        with (
            patch.object(setup_router, "_MODELS_DIR", tmp_path),
            patch.object(
                setup_router,
                "_fetch_json",
                AsyncMock(return_value=manifest or _catalog_manifest()),
            ),
            patch.object(setup_router.httpx, "AsyncClient", fake),
            patch.object(setup_router, "_apply_detection_config", apply),
            patch.object(setup_router, "get_config", return_value=AppConfig()),
        ):
            return client.post(
                "/api/detection/catalog/install",
                json={"name": "tiny-int8.onnx", **body},
            )

    def test_downloads_verifies_and_activates(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [_MODEL_BYTES]))
        apply = AsyncMock()

        res = self._post(client, tmp_path, fake, apply)

        assert res.status_code == 200
        model_path = tmp_path / "tiny-int8.onnx"
        assert res.json() == {
            "model_path": str(model_path),
            "sha256": _MODEL_SHA256,
            "active": True,
        }
        assert model_path.read_bytes() == _MODEL_BYTES
        assert fake.requested_urls == [_ASSET_URL]
        applied = apply.call_args.args[0]
        assert applied.backend == "onnx"
        assert applied.model_path == str(model_path)

    def test_activate_false_only_installs(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [_MODEL_BYTES]))
        apply = AsyncMock()

        res = self._post(client, tmp_path, fake, apply, activate=False)

        assert res.status_code == 200
        assert res.json()["active"] is False
        apply.assert_not_called()

    def test_checksum_mismatch_rejects_download_and_keeps_old_file(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "tiny-int8.onnx").write_bytes(b"previous")
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"tampered!"]))
        apply = AsyncMock()

        res = self._post(client, tmp_path, fake, apply)

        assert res.status_code == 502
        assert "Checksum mismatch" in res.json()["detail"]
        assert (tmp_path / "tiny-int8.onnx").read_bytes() == b"previous"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["tiny-int8.onnx"]
        apply.assert_not_called()

    def test_reuses_file_already_on_disk_with_matching_sha256(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "tiny-int8.onnx").write_bytes(_MODEL_BYTES)
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"unused"]))

        res = self._post(client, tmp_path, fake, AsyncMock())

        assert res.status_code == 200
        assert fake.requested_urls == []

    def test_unknown_model_returns_404(self, client: TestClient) -> None:
        fake = _FakeAsyncClient()
        with (
            patch.object(
                setup_router, "_fetch_json", AsyncMock(return_value=_catalog_manifest())
            ),
            patch.object(setup_router.httpx, "AsyncClient", fake),
        ):
            res = client.post(
                "/api/detection/catalog/install", json={"name": "nope.onnx"}
            )

        assert res.status_code == 404
        assert fake.called is False


@pytest.mark.usefixtures("resolve_host")
class TestFetchJson:
    async def test_decodes_json(self) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b'{"a": ', b"1}"]))
        with patch.object(setup_router.httpx, "AsyncClient", fake):
            assert await setup_router._fetch_json(
                "https://example.com/m.json", 100
            ) == {"a": 1}

    async def test_rejects_oversized_response(self) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"x" * 50]))
        with (
            patch.object(setup_router.httpx, "AsyncClient", fake),
            pytest.raises(HTTPException) as exc_info,
        ):
            await setup_router._fetch_json("https://example.com/m.json", 10)
        assert exc_info.value.status_code == 502

    async def test_rejects_invalid_json(self) -> None:
        fake = _FakeAsyncClient(response=_FakeFetchResponse(200, [b"not json"]))
        with (
            patch.object(setup_router.httpx, "AsyncClient", fake),
            pytest.raises(HTTPException, match="not valid JSON"),
        ):
            await setup_router._fetch_json("https://example.com/m.json", 100)
