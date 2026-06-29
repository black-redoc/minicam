import pytest

from app.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "camera_host": "192.168.1.10",
        "camera_password": "secret123",
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


class TestSettingsDefaults:
    def test_default_rtsp_port(self) -> None:
        s = _make_settings()
        assert s.camera_rtsp_port == 554

    def test_default_user(self) -> None:
        s = _make_settings()
        assert s.camera_user == "admin"

    def test_default_channel(self) -> None:
        s = _make_settings()
        assert s.camera_channel == 1

    def test_default_stream(self) -> None:
        s = _make_settings()
        assert s.camera_stream == "main"

    def test_default_onvif_port(self) -> None:
        s = _make_settings()
        assert s.camera_onvif_port == 8000

    def test_default_app_port(self) -> None:
        s = _make_settings()
        assert s.app_port == 8080

    def test_debug_field_default_is_false(self) -> None:
        assert Settings.model_fields["debug"].default is False


class TestGetSettings:
    def test_get_settings_returns_settings_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAMERA_HOST", "10.0.0.1")
        monkeypatch.setenv("CAMERA_PASSWORD", "pass")
        result = get_settings()
        assert isinstance(result, Settings)

    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAMERA_HOST", "10.0.0.1")
        monkeypatch.setenv("CAMERA_PASSWORD", "pass")
        assert get_settings() is get_settings()


class TestRtspUrl:
    def test_url_contains_host(self) -> None:
        s = _make_settings(camera_host="10.0.0.5")
        assert "10.0.0.5" in s.rtsp_url

    def test_url_contains_user(self) -> None:
        s = _make_settings(camera_user="root")
        assert "root:" in s.rtsp_url

    def test_url_contains_password(self) -> None:
        s = _make_settings(camera_password="mypass")
        assert "mypass@" in s.rtsp_url

    def test_url_contains_rtsp_port(self) -> None:
        s = _make_settings(camera_rtsp_port=8554)
        assert ":8554/" in s.rtsp_url

    def test_url_contains_channel(self) -> None:
        s = _make_settings(camera_channel=2)
        assert "/ch2/" in s.rtsp_url

    def test_url_contains_stream(self) -> None:
        s = _make_settings(camera_stream="sub")
        assert "/sub/" in s.rtsp_url

    def test_url_starts_with_rtsp_scheme(self) -> None:
        s = _make_settings()
        assert s.rtsp_url.startswith("rtsp://")

    def test_url_ends_with_av_stream(self) -> None:
        s = _make_settings()
        assert s.rtsp_url.endswith("/av_stream")
