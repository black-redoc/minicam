"""Tests for app.services.config_writer (TOML-based read/write)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import tomli_w

import app.services.config_writer as config_writer_module
from app.models.config import (
    AppConfig,
    CameraConfig,
    DetectionConfig,
    NotificationConfig,
)
from app.services.config_writer import (
    add_camera,
    load_config,
    remove_camera,
    save_config,
    update_camera,
    update_detection_config,
    update_notification_config,
)


def _make_camera(name: str = "cam1", host: str = "192.168.1.10") -> CameraConfig:
    return CameraConfig(name=name, host=host, password="secret")


def _write_toml(path: Path, doc: dict[str, object]) -> None:
    path.write_bytes(tomli_w.dumps(doc).encode())


@pytest.fixture()
def toml_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    _write_toml(
        path,
        {
            "app": {"host": "0.0.0.0", "port": 8080, "debug": False},
            "cameras": [
                {
                    "name": "cam1",
                    "host": "192.168.1.10",
                    "rtsp_port": 554,
                    "onvif_port": 80,
                    "user": "admin",
                    "password": "secret",
                    "channel": 1,
                    "stream": "main",
                }
            ],
        },
    )
    monkeypatch.setattr(config_writer_module, "_CONFIG_PATH", path)
    return path


class TestLoadConfig:
    def test_returns_appconfig_from_valid_toml(self, toml_file: Path) -> None:
        result = load_config(toml_file)
        assert isinstance(result, AppConfig)
        assert len(result.cameras) == 1
        assert result.cameras[0].name == "cam1"

    def test_returns_defaults_when_file_missing(self, tmp_path: Path) -> None:
        result = load_config(tmp_path / "missing.toml")
        assert isinstance(result, AppConfig)
        assert result.cameras == []

    def test_returns_defaults_for_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_bytes(b"")
        result = load_config(path)
        assert isinstance(result, AppConfig)
        assert result.cameras == []


class TestSaveConfig:
    def test_writes_readable_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        config = AppConfig(host="127.0.0.1", port=9090, debug=True)
        save_config(config, path)
        with open(path, "rb") as f:
            data = tomllib.load(f)
        assert data["app"]["host"] == "127.0.0.1"
        assert data["app"]["port"] == 9090

    def test_applies_chmod_600(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        config = AppConfig()
        save_config(config, path)
        mode = oct(path.stat().st_mode)
        assert mode.endswith("600")

    def test_does_not_include_rtsp_url(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        cam = CameraConfig(name="cam1", host="10.0.0.1", password="pass")
        config = AppConfig(cameras=[cam])
        save_config(config, path)
        content = path.read_text()
        assert "rtsp_url" not in content

    def test_writes_detection_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        config = AppConfig(
            detection=DetectionConfig(
                backend="onnx",
                model_path="/models/yolo.onnx",
                confidence_threshold=0.6,
                inference_fps=4.0,
            )
        )
        save_config(config, path)
        with open(path, "rb") as f:
            data = tomllib.load(f)
        assert data["detection"]["backend"] == "onnx"
        assert data["detection"]["model_path"] == "/models/yolo.onnx"
        assert data["detection"]["confidence_threshold"] == 0.6
        assert data["detection"]["inference_fps"] == 4.0

    def test_tightens_permissions_of_existing_world_readable_file(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "config.toml"
        path.write_text("")
        path.chmod(0o644)

        save_config(AppConfig(cameras=[_make_camera()]), path)

        assert path.stat().st_mode & 0o777 == 0o600

    def test_never_writes_target_in_place(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("")
        original_inode = path.stat().st_ino

        save_config(AppConfig(cameras=[_make_camera()]), path)

        assert path.stat().st_ino != original_inode

    def test_failed_write_keeps_previous_config_and_leaves_no_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "config.toml"
        save_config(AppConfig(cameras=[_make_camera("before")]), path)
        previous = path.read_bytes()

        def failing_replace(src: str, dst: Path) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(config_writer_module.os, "replace", failing_replace)
        with pytest.raises(OSError, match="disk full"):
            save_config(AppConfig(cameras=[_make_camera("after")]), path)

        assert path.read_bytes() == previous
        assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]


class TestDetectionRoundTrip:
    def test_round_trips_detection_config(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        original = AppConfig(
            detection=DetectionConfig(
                backend="onnx",
                model_path="/models/yolo.onnx",
                confidence_threshold=0.6,
                inference_fps=4.0,
            )
        )
        save_config(original, path)
        restored = load_config(path)
        assert restored.detection == original.detection

    def test_missing_detection_section_uses_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        _write_toml(path, {"app": {"host": "0.0.0.0", "port": 8080, "debug": False}})
        restored = load_config(path)
        assert restored.detection == DetectionConfig()


class TestAddCamera:
    def test_adds_camera(self, toml_file: Path) -> None:
        cam = _make_camera(name="cam2", host="10.0.0.2")
        result = add_camera(cam, toml_file)
        assert len(result.cameras) == 2
        assert any(c.name == "cam2" for c in result.cameras)

    def test_raises_value_error_for_duplicate_name(self, toml_file: Path) -> None:
        cam = _make_camera(name="cam1")
        with pytest.raises(ValueError, match="cam1"):
            add_camera(cam, toml_file)


class TestUpdateCamera:
    def test_replaces_camera(self, toml_file: Path) -> None:
        updated = CameraConfig(name="cam1", host="10.0.0.99", password="newpass")
        result = update_camera("cam1", updated, toml_file)
        assert result.cameras[0].host == "10.0.0.99"

    def test_raises_key_error_for_missing_name(self, toml_file: Path) -> None:
        cam = _make_camera(name="ghost")
        with pytest.raises(KeyError, match="ghost"):
            update_camera("ghost", cam, toml_file)


class TestRemoveCamera:
    def test_removes_camera(self, toml_file: Path) -> None:
        result = remove_camera("cam1", toml_file)
        assert len(result.cameras) == 0

    def test_raises_key_error_for_missing_name(self, toml_file: Path) -> None:
        with pytest.raises(KeyError, match="ghost"):
            remove_camera("ghost", toml_file)


class TestUpdateDetectionConfig:
    def test_creates_detection_section_when_absent(self, toml_file: Path) -> None:
        detection = DetectionConfig(backend="onnx", model_path="/models/yolo.onnx")
        result = update_detection_config(detection, toml_file)
        assert result.detection == detection

    def test_persists_to_disk(self, toml_file: Path) -> None:
        detection = DetectionConfig(backend="onnx", model_path="/models/yolo.onnx")
        update_detection_config(detection, toml_file)
        reloaded = load_config(toml_file)
        assert reloaded.detection == detection

    def test_updates_existing_detection_section(self, toml_file: Path) -> None:
        update_detection_config(
            DetectionConfig(backend="onnx", model_path="/a.onnx"), toml_file
        )
        updated = update_detection_config(
            DetectionConfig(backend="onnx", model_path="/b.onnx"), toml_file
        )
        assert updated.detection.model_path == "/b.onnx"

    def test_preserves_cameras(self, toml_file: Path) -> None:
        detection = DetectionConfig(backend="onnx", model_path="/models/yolo.onnx")
        result = update_detection_config(detection, toml_file)
        assert len(result.cameras) == 1
        assert result.cameras[0].name == "cam1"


class TestUpdateNotificationConfig:
    def test_persists_and_preserves_cameras(self, toml_file: Path) -> None:
        settings = NotificationConfig(enabled=True, cooldown_seconds=45)
        result = update_notification_config(settings, toml_file)
        assert result.notifications == settings
        reloaded = load_config(toml_file)
        assert reloaded.notifications == settings
        assert [c.name for c in reloaded.cameras] == ["cam1"]

    def test_camera_notification_mode_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        camera = CameraConfig(name="cam1", host="h", notifications="on")
        save_config(AppConfig(cameras=[camera]), path)
        assert load_config(path).cameras[0].notifications == "on"
