"""Tests for app.settings and app.models.config."""

from __future__ import annotations

import tomli_w
from pathlib import Path

import pytest

from app.models.config import AppConfig, CameraConfig
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

    def test_default_stream(self) -> None:
        cam = _make_camera()
        assert cam.stream == "main"


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

    def test_url_contains_stream(self) -> None:
        cam = _make_camera(stream="sub")
        assert "/sub/" in cam.rtsp_url

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
                        "stream": "main",
                    }
                ],
            },
        )
        result = get_config(str(path))
        assert len(result.cameras) == 1
        assert result.cameras[0].name == "cam1"
        assert result.cameras[0].host == "10.0.0.1"

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
