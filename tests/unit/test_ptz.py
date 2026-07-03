import threading
import time
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


class TestPTZServiceEnsureConnected:
    def test_connects_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        with patch.object(svc, "connect") as mock_connect:
            svc.ensure_connected()
        mock_connect.assert_called_once()

    def test_skips_connect_when_already_connected(self) -> None:
        svc = _make_connected_service()
        with patch.object(svc, "connect") as mock_connect:
            svc.ensure_connected()
        mock_connect.assert_not_called()

    def test_concurrent_callers_connect_only_once(self) -> None:
        """Regression test: lazy-connect must be race-free under concurrency.

        Before the lock was added, several requests arriving while the first
        connect() was still in flight would each see is_connected() == False
        and all call connect() themselves.
        """
        svc = PTZService(_make_config())
        call_count = 0

        def slow_connect() -> None:
            nonlocal call_count
            call_count += 1
            time.sleep(0.05)
            svc._ptz = MagicMock()
            svc._profile_token = "token"

        with patch.object(svc, "connect", side_effect=slow_connect):
            threads = [
                threading.Thread(target=svc.ensure_connected) for _ in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert call_count == 1


class TestPTZServiceReconnect:
    def test_disconnects_then_connects(self) -> None:
        svc = _make_connected_service()
        calls: list[str] = []

        def _record(label: str) -> None:
            calls.append(label)

        with (
            patch.object(svc, "disconnect", side_effect=lambda: _record("disconnect")),
            patch.object(svc, "connect", side_effect=lambda: _record("connect")),
        ):
            svc.reconnect()
        assert calls == ["disconnect", "connect"]


class TestPTZServiceRunCommand:
    def test_connects_then_executes_when_not_connected(self) -> None:
        svc = PTZService(_make_config())
        calls: list[str] = []

        def fake_connect() -> None:
            calls.append("connect")
            svc._ptz = MagicMock()
            svc._profile_token = "token"

        with patch.object(svc, "connect", side_effect=fake_connect):
            svc.run_command("stop")

        assert calls == ["connect"]
        svc._ptz.Stop.assert_called_once()

    def test_skips_connect_when_already_connected(self) -> None:
        svc = _make_connected_service()
        with patch.object(svc, "connect") as mock_connect:
            svc.run_command("stop")
        mock_connect.assert_not_called()
        svc._ptz.Stop.assert_called_once()

    def test_concurrent_commands_never_overlap(self) -> None:
        """Regression test: a burst of rapid clicks must serialize ONVIF calls.

        Before the lock covered the action too (not just connect), several
        concurrent requests for the same camera — exactly what happens when a
        user clicks a PTZ button several times quickly — could interleave
        calls into the shared (non thread-safe) zeep SOAP client.
        """
        svc = _make_connected_service()
        active = 0
        max_active = 0
        guard = threading.Lock()

        def fake_move(*_args: object, **_kwargs: object) -> None:
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with guard:
                active -= 1

        svc._ptz.ContinuousMove.side_effect = fake_move

        threads = [
            threading.Thread(target=svc.run_command, args=("up",)) for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert max_active == 1
        assert svc._ptz.ContinuousMove.call_count == 10


class TestPTZServiceReconnectAndRun:
    def test_disconnects_connects_then_executes(self) -> None:
        svc = _make_connected_service()
        calls: list[str] = []

        def fake_connect() -> None:
            calls.append("connect")
            svc._ptz = MagicMock()
            svc._profile_token = "token"

        def fake_disconnect() -> None:
            calls.append("disconnect")

        with (
            patch.object(svc, "disconnect", side_effect=fake_disconnect),
            patch.object(svc, "connect", side_effect=fake_connect),
        ):
            svc.reconnect_and_run("stop")

        assert calls == ["disconnect", "connect"]
        svc._ptz.Stop.assert_called_once()
