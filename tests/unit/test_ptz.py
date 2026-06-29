from unittest.mock import MagicMock, patch

import pytest

from app.services.ptz import PTZConfig, PTZService


def _make_config(**overrides: object) -> PTZConfig:
    defaults: dict[str, object] = {
        "host": "192.168.1.10",
        "port": 8000,
        "user": "admin",
        "password": "secret",
    }
    defaults.update(overrides)
    return PTZConfig(**defaults)  # type: ignore[arg-type]


def _make_connected_service() -> PTZService:
    """Return a PTZService with a mocked ONVIF connection."""
    svc = PTZService(_make_config())

    mock_profile = MagicMock()
    mock_profile.token = "profile_token_1"

    mock_media = MagicMock()
    mock_media.GetProfiles.return_value = [mock_profile]

    mock_ptz = MagicMock()

    mock_camera = MagicMock()
    mock_camera.create_ptz_service.return_value = mock_ptz
    mock_camera.create_media_service.return_value = mock_media

    with patch("app.services.ptz.ONVIFCamera", return_value=mock_camera):
        svc.connect()

    return svc


class TestPTZConfig:
    def test_stores_host(self) -> None:
        cfg = _make_config(host="10.0.0.1")
        assert cfg.host == "10.0.0.1"

    def test_stores_port(self) -> None:
        cfg = _make_config(port=9000)
        assert cfg.port == 9000

    def test_is_immutable(self) -> None:
        cfg = _make_config()
        with pytest.raises(AttributeError):
            cfg.host = "other"  # type: ignore[misc]


class TestPTZServiceNotConnected:
    def test_up_raises_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            svc.up()

    def test_down_raises_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            svc.down()

    def test_left_raises_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            svc.left()

    def test_right_raises_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            svc.right()

    def test_stop_raises_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            svc.stop()


class TestPTZServiceConnected:
    def test_connect_sets_profile_token(self) -> None:
        svc = _make_connected_service()
        assert svc._profile_token == "profile_token_1"

    def test_disconnect_clears_ptz(self) -> None:
        svc = _make_connected_service()
        svc.disconnect()
        assert svc._ptz is None
        assert svc._profile_token is None

    def test_up_calls_continuous_move(self) -> None:
        svc = _make_connected_service()
        svc.up()
        svc._ptz.ContinuousMove.assert_called_once()

    def test_down_calls_continuous_move(self) -> None:
        svc = _make_connected_service()
        svc.down()
        svc._ptz.ContinuousMove.assert_called_once()

    def test_left_calls_continuous_move(self) -> None:
        svc = _make_connected_service()
        svc.left()
        svc._ptz.ContinuousMove.assert_called_once()

    def test_right_calls_continuous_move(self) -> None:
        svc = _make_connected_service()
        svc.right()
        svc._ptz.ContinuousMove.assert_called_once()

    def test_stop_calls_ptz_stop(self) -> None:
        svc = _make_connected_service()
        svc.stop()
        svc._ptz.Stop.assert_called_once()
