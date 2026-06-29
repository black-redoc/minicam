"""Tests for the setup router: GET /api/config, PUT /api/config, GET /api/setup/discover."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
import app.settings as settings_mod
from app.models.config import DiscoveredCamera
from app.routers import setup as setup_router
from app.settings import Settings, get_settings


def _make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "camera_host": "192.168.1.10",
        "camera_password": "secret123",
        "camera_rtsp_port": 554,
        "camera_user": "admin",
        "camera_channel": 1,
        "camera_stream": "main",
        "camera_onvif_port": 8000,
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


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
def client(
    monkeypatch: pytest.MonkeyPatch,
    mock_camera: MagicMock,
    mock_ptz: MagicMock,
) -> TestClient:
    monkeypatch.setattr(state_module, "camera", mock_camera)
    monkeypatch.setattr(state_module, "ptz", mock_ptz)
    test_app = FastAPI()
    test_app.include_router(setup_router.router)
    return TestClient(test_app)


class TestGetConfig:
    def test_returns_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        res = client.get("/api/config")
        assert res.status_code == 200

    def test_password_is_always_empty(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        res = client.get("/api/config")
        assert res.json()["camera_password"] == ""

    def test_returns_correct_fields(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            setup_router,
            "get_settings",
            lambda: _make_settings(camera_host="10.0.0.5", camera_rtsp_port=8554),
        )
        data = client.get("/api/config").json()
        assert data["camera_host"] == "10.0.0.5"
        assert data["camera_rtsp_port"] == 8554
        assert data["camera_user"] == "admin"
        assert data["camera_stream"] == "main"
        assert data["camera_onvif_port"] == 8000

    def test_camera_host_none_returns_empty_string(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings(camera_host=None)
        )
        data = client.get("/api/config").json()
        assert data["camera_host"] == ""


class TestPutConfig:
    _valid_body: dict[str, object] = {
        "camera_host": "10.0.0.99",
        "camera_rtsp_port": 554,
        "camera_user": "admin",
        "camera_password": "newpass",
        "camera_channel": 1,
        "camera_stream": "main",
        "camera_onvif_port": 8000,
    }

    def test_returns_ok(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_router, "write_env_config", MagicMock())
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        res = client.put("/api/config", json=self._valid_body)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_calls_write_env_config(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_write = MagicMock()
        monkeypatch.setattr(setup_router, "write_env_config", mock_write)
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        client.put("/api/config", json=self._valid_body)
        mock_write.assert_called_once()

    def test_password_included_when_non_empty(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_write = MagicMock()
        monkeypatch.setattr(setup_router, "write_env_config", mock_write)
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        client.put("/api/config", json=self._valid_body)
        call_args = mock_write.call_args[0][0]
        assert "CAMERA_PASSWORD" in call_args
        assert call_args["CAMERA_PASSWORD"] == "newpass"

    def test_password_excluded_when_empty(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_write = MagicMock()
        monkeypatch.setattr(setup_router, "write_env_config", mock_write)
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        body = {**self._valid_body, "camera_password": ""}
        client.put("/api/config", json=body)
        call_args = mock_write.call_args[0][0]
        assert "CAMERA_PASSWORD" not in call_args

    def test_clears_settings_cache(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_write = MagicMock()
        monkeypatch.setattr(setup_router, "write_env_config", mock_write)
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        mock_clear = MagicMock()
        # The router calls _settings_mod.get_settings.cache_clear() to bypass patching.
        monkeypatch.setattr(settings_mod.get_settings, "cache_clear", mock_clear)
        client.put("/api/config", json=self._valid_body)
        mock_clear.assert_called_once()

    def test_stops_existing_camera(
        self,
        client: TestClient,
        mock_camera: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(setup_router, "write_env_config", MagicMock())
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        client.put("/api/config", json=self._valid_body)
        mock_camera.stop.assert_called_once()

    def test_disconnects_existing_ptz(
        self,
        client: TestClient,
        mock_ptz: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(setup_router, "write_env_config", MagicMock())
        monkeypatch.setattr(
            setup_router, "get_settings", lambda: _make_settings()
        )
        client.put("/api/config", json=self._valid_body)
        mock_ptz.disconnect.assert_called_once()

    def test_creates_new_camera_when_host_set(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(setup_router, "write_env_config", MagicMock())
        new_settings = _make_settings(camera_host="10.0.0.99")
        monkeypatch.setattr(setup_router, "get_settings", lambda: new_settings)

        mock_cam_cls = MagicMock()
        with patch("app.routers.setup.RTSPCamera", mock_cam_cls):
            client.put("/api/config", json=self._valid_body)

        mock_cam_cls.assert_called_once()
        mock_cam_cls.return_value.start.assert_called_once()

    def test_creates_new_ptz_when_host_set(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(setup_router, "write_env_config", MagicMock())
        new_settings = _make_settings(camera_host="10.0.0.99")
        monkeypatch.setattr(setup_router, "get_settings", lambda: new_settings)

        mock_ptz_cls = MagicMock()
        with patch("app.routers.setup.PTZService", mock_ptz_cls):
            client.put("/api/config", json=self._valid_body)

        mock_ptz_cls.assert_called_once()


class TestDiscover:
    def test_returns_200(self, client: TestClient) -> None:
        with patch(
            "app.routers.setup.discover_onvif_cameras", return_value=[]
        ):
            res = client.get("/api/setup/discover")
        assert res.status_code == 200

    def test_returns_empty_list_when_no_cameras(self, client: TestClient) -> None:
        with patch(
            "app.routers.setup.discover_onvif_cameras", return_value=[]
        ):
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
        with patch(
            "app.routers.setup.discover_onvif_cameras", return_value=cameras
        ):
            res = client.get("/api/setup/discover")
        data = res.json()
        assert len(data) == 1
        assert data[0]["host"] == "192.168.1.50"
        assert data[0]["name"] == "IPCamera"
        assert data[0]["hardware"] == "DS-2CD"
        assert data[0]["location"] == "Garage"
