"""Tests for app.services.config_writer (TOML-based read/write)."""

from __future__ import annotations

import tomllib
import tomli_w
from pathlib import Path

import pytest

import app.services.config_writer as config_writer_module
from app.models.config import AppConfig, CameraConfig
from app.services.config_writer import (
    add_camera,
    load_config,
    remove_camera,
    save_config,
    update_camera,
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
