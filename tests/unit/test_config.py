"""Tests for app.settings and app.models.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w
from pydantic import ValidationError

from app.models.config import (
    AppConfig,
    CameraConfig,
    CameraConfigRequest,
    CameraConfigResponse,
    DetectionConfig,
)
from app.settings import get_config


@pytest.fixture(autouse=True)
def clear_config_cache() -> pytest.FixtureFunction:
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make_camera(**overrides: object) -> CameraConfig:
    defaults: dict[str, object] = {
        "name": "test",
        "host": "192.168.1.10",
        "password": "secret123",
    }
    defaults.update(overrides)
    return CameraConfig.model_validate(defaults)


def _write_toml(path: Path, doc: dict[str, object]) -> None:
    path.write_bytes(tomli_w.dumps(doc).encode())


class TestAppConfigDefaults:
    def test_default_host(self) -> None:
        config = AppConfig()
        assert config.host == "0.0.0.0"

    def test_default_port(self) -> None:
        config = AppConfig()
        assert config.port == 8080

    def test_default_debug_is_false(self) -> None:
        config = AppConfig()
        assert config.debug is False

    def test_default_cameras_is_empty(self) -> None:
        config = AppConfig()
        assert config.cameras == []

    def test_default_detection_is_default_detection_config(self) -> None:
        config = AppConfig()
        assert config.detection == DetectionConfig()


class TestCameraConfigDefaults:
    def test_default_rtsp_port(self) -> None:
        cam = _make_camera()
        assert cam.rtsp_port == 554

    def test_default_onvif_port(self) -> None:
        cam = _make_camera()
        assert cam.onvif_port == 80

    def test_default_user(self) -> None:
        cam = _make_camera()
        assert cam.user == "admin"

    def test_default_channel(self) -> None:
        cam = _make_camera()
        assert cam.channel == 1

    def test_default_detection_classes_is_empty(self) -> None:
        cam = _make_camera()
        assert cam.detection_classes == []

    def test_detection_classes_accepts_list(self) -> None:
        cam = _make_camera(detection_classes=["person", "dog"])
        assert cam.detection_classes == ["person", "dog"]


class TestDetectionClassesSerialization:
    def test_camera_config_round_trips_detection_classes(self) -> None:
        cam = _make_camera(detection_classes=["person"])
        restored = CameraConfig.model_validate(cam.model_dump())
        assert restored.detection_classes == ["person"]

    def test_response_default_detection_classes_is_empty(self) -> None:
        response = CameraConfigResponse(
            name="cam1",
            host="10.0.0.1",
            rtsp_port=554,
            onvif_port=80,
            user="admin",
            password="secret",
            channel=1,
        )
        assert response.detection_classes == []

    def test_response_accepts_detection_classes(self) -> None:
        response = CameraConfigResponse(
            name="cam1",
            host="10.0.0.1",
            rtsp_port=554,
            onvif_port=80,
            user="admin",
            password="secret",
            channel=1,
            detection_classes=["cat"],
        )
        assert response.detection_classes == ["cat"]

    def test_request_default_detection_classes_is_empty(self) -> None:
        request = CameraConfigRequest(name="cam1", host="10.0.0.1")
        assert request.detection_classes == []

    def test_request_accepts_detection_classes(self) -> None:
        request = CameraConfigRequest(
            name="cam1", host="10.0.0.1", detection_classes=["person", "car"]
        )
        assert request.detection_classes == ["person", "car"]


class TestDetectionConfigDefaults:
    def test_default_backend_is_none(self) -> None:
        detection = DetectionConfig()
        assert detection.backend == "none"

    def test_default_model_path_is_empty(self) -> None:
        detection = DetectionConfig()
        assert detection.model_path == ""

    def test_default_confidence_threshold(self) -> None:
        detection = DetectionConfig()
        assert detection.confidence_threshold == 0.5

    def test_default_inference_fps(self) -> None:
        detection = DetectionConfig()
        assert detection.inference_fps == 3.0

    def test_confidence_threshold_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            DetectionConfig(confidence_threshold=1.1)

    def test_confidence_threshold_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            DetectionConfig(confidence_threshold=-0.1)

    def test_inference_fps_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            DetectionConfig(inference_fps=0.0)

    def test_inference_fps_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            DetectionConfig(inference_fps=-1.0)


class TestRtspUrl:
    def test_url_starts_with_rtsp_scheme(self) -> None:
        cam = _make_camera()
        assert cam.rtsp_url.startswith("rtsp://")

    def test_url_contains_host(self) -> None:
        cam = _make_camera(host="10.0.0.5")
        assert "10.0.0.5" in cam.rtsp_url

    def test_url_contains_user(self) -> None:
        cam = _make_camera(user="root")
        assert "root:" in cam.rtsp_url

    def test_url_contains_password(self) -> None:
        cam = _make_camera(password="mypass")
        assert "mypass@" in cam.rtsp_url

    def test_url_contains_rtsp_port(self) -> None:
        cam = _make_camera(rtsp_port=8554)
        assert ":8554/" in cam.rtsp_url

    def test_url_contains_channel(self) -> None:
        cam = _make_camera(channel=2)
        assert "/ch2/" in cam.rtsp_url

    def test_url_always_uses_sub_stream(self) -> None:
        cam = _make_camera()
        assert "/ch1/sub/av_stream" in cam.rtsp_url

    def test_legacy_stream_key_is_ignored(self) -> None:
        cam = CameraConfig.model_validate(
            {"name": "cam1", "host": "10.0.0.1", "stream": "main"}
        )
        assert "/sub/" in cam.rtsp_url
        assert "stream" not in cam.model_dump()

    def test_url_ends_with_av_stream(self) -> None:
        cam = _make_camera()
        assert cam.rtsp_url.endswith("/av_stream")


class TestGetConfig:
    def test_returns_defaults_when_file_missing(self, tmp_path: Path) -> None:
        path = str(tmp_path / "nonexistent.toml")
        result = get_config(path)
        assert isinstance(result, AppConfig)
        assert result.cameras == []

    def test_loads_app_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(path, {"app": {"host": "127.0.0.1", "port": 9090, "debug": True}})
        result = get_config(str(path))
        assert result.host == "127.0.0.1"
        assert result.port == 9090
        assert result.debug is True

    def test_loads_cameras(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(
            path,
            {
                "app": {"host": "0.0.0.0", "port": 8080, "debug": False},
                "cameras": [
                    {
                        "name": "cam1",
                        "host": "10.0.0.1",
                        "rtsp_port": 554,
                        "onvif_port": 80,
                        "user": "admin",
                        "password": "pass",
                        "channel": 1,
                    }
                ],
            },
        )
        result = get_config(str(path))
        assert len(result.cameras) == 1
        assert result.cameras[0].name == "cam1"
        assert result.cameras[0].host == "10.0.0.1"

    def test_loads_detection_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(
            path,
            {
                "app": {"host": "0.0.0.0", "port": 8080, "debug": False},
                "detection": {
                    "backend": "onnx",
                    "model_path": "/models/yolo.onnx",
                    "confidence_threshold": 0.7,
                    "inference_fps": 5.0,
                },
            },
        )
        result = get_config(str(path))
        assert result.detection.backend == "onnx"
        assert result.detection.model_path == "/models/yolo.onnx"
        assert result.detection.confidence_threshold == 0.7
        assert result.detection.inference_fps == 5.0

    def test_detection_defaults_when_section_absent(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(path, {"app": {"host": "0.0.0.0", "port": 8080, "debug": False}})
        result = get_config(str(path))
        assert result.detection == DetectionConfig()

    def test_is_cached(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(path, {})
        result1 = get_config(str(path))
        result2 = get_config(str(path))
        assert result1 is result2

    def test_cache_clear_works(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(path, {})
        result1 = get_config(str(path))
        get_config.cache_clear()
        result2 = get_config(str(path))
        assert result1 is not result2


class TestNotificationConfig:
    def test_defaults_are_off_with_60s_cooldown(self) -> None:
        config = AppConfig()
        assert config.notifications.enabled is False
        assert config.notifications.cooldown_seconds == 60
        assert CameraConfig(name="c", host="h").notifications == "inherit"

    def test_rejects_unknown_camera_mode(self) -> None:
        with pytest.raises(ValidationError):
            CameraConfig(name="c", host="h", notifications="maybe")  # type: ignore[arg-type]

    def test_get_config_loads_notifications_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(
            path,
            {
                "cameras": [{"name": "c", "host": "h", "notifications": "off"}],
                "notifications": {"enabled": True, "cooldown_seconds": 90},
            },
        )
        result = get_config(str(path))
        assert result.notifications.enabled is True
        assert result.notifications.cooldown_seconds == 90
        assert result.cameras[0].notifications == "off"
