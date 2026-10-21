import logging
import time
from unittest.mock import MagicMock, patch

import pytest
from av.codec.context import Flags

from app.buffers.frame_buffer import FrameBuffer
from app.services.camera import RTSPCamera, reconnect_delay, redact_url
from tests.fixtures.camera import make_frame, make_mock_av_frame


def _make_camera(
    url: str = "rtsp://fake/stream",
) -> tuple[RTSPCamera, FrameBuffer]:
    buf = FrameBuffer()
    cam = RTSPCamera(rtsp_url=url, buffer=buf)
    return cam, buf


class TestRTSPCameraInitialState:
    def test_is_not_connected_initially(self) -> None:
        cam, _ = _make_camera()
        assert cam.is_connected() is False

    def test_is_not_running_initially(self) -> None:
        cam, _ = _make_camera()
        assert cam._running is False


class TestRTSPCameraStartStop:
    def test_start_sets_running(self) -> None:
        cam, _ = _make_camera()
        with patch(
            "app.services.camera.av.open", side_effect=ConnectionError("no cam")
        ):
            cam.start()
            assert cam._running is True
            cam.stop()

    def test_stop_sets_running_false(self) -> None:
        cam, _ = _make_camera()
        with patch(
            "app.services.camera.av.open", side_effect=ConnectionError("no cam")
        ):
            cam.start()
            cam.stop()
            assert cam._running is False

    def test_double_start_does_not_raise(self) -> None:
        cam, _ = _make_camera()
        with patch(
            "app.services.camera.av.open", side_effect=ConnectionError("no cam")
        ):
            cam.start()
            cam.start()
            cam.stop()

    def test_stop_without_start_does_not_raise(self) -> None:
        cam, _ = _make_camera()
        cam.stop()

    def test_is_not_connected_after_stop(self) -> None:
        cam, _ = _make_camera()
        with patch(
            "app.services.camera.av.open", side_effect=ConnectionError("no cam")
        ):
            cam.start()
            cam.stop()
            assert cam.is_connected() is False


class TestRTSPCameraFrameCapture:
    def test_frames_are_pushed_to_buffer(self) -> None:
        cam, buf = _make_camera()

        mock_av_frame = make_mock_av_frame()
        mock_packet = MagicMock()
        mock_packet.decode.return_value = [mock_av_frame]

        mock_video_stream = MagicMock()
        mock_video_stream.type = "video"
        mock_video_stream.codec_context = MagicMock()

        mock_container = MagicMock()
        mock_container.streams = [mock_video_stream]
        mock_container.demux.return_value = [mock_packet]

        with patch("app.services.camera.av.open", return_value=mock_container):
            cam.start()
            time.sleep(0.1)
            cam.stop()

        time.sleep(0.05)
        assert buf.read() is not None


class TestRTSPCameraEdgeCases:
    def test_capture_stream_returns_early_when_not_running(self) -> None:
        cam, _ = _make_camera()
        cam._running = False
        with patch("app.services.camera.av.open") as mock_open:
            cam._capture_stream()
            mock_open.assert_not_called()

    def test_stop_handles_container_close_exception(self) -> None:
        cam, _ = _make_camera()
        mock_container = MagicMock()
        mock_container.close.side_effect = RuntimeError("close failed")
        cam._container = mock_container
        cam.stop()  # should not raise

    def test_stop_logs_warning_when_thread_does_not_stop(self) -> None:
        cam, _ = _make_camera()
        mock_thread = MagicMock(spec=["join", "is_alive"])
        mock_thread.is_alive.return_value = True
        cam._thread = mock_thread
        cam.stop()  # should not raise

    def test_capture_stream_finally_handles_container_close_exception(self) -> None:
        cam, _ = _make_camera()
        cam._running = True

        mock_container = MagicMock()
        mock_container.streams = [MagicMock(type="video", codec_context=MagicMock())]
        mock_container.demux.return_value = []
        mock_container.close.side_effect = RuntimeError("close error")

        with patch("app.services.camera.av.open", return_value=mock_container):
            cam._capture_stream()  # should not raise even if close() fails

    def test_capture_stream_applies_low_delay_flag(self) -> None:
        cam, _ = _make_camera()
        cam._running = True

        mock_video_stream = MagicMock()
        mock_video_stream.type = "video"
        mock_video_stream.codec_context = MagicMock()
        original_flags = mock_video_stream.codec_context.flags

        mock_container = MagicMock()
        mock_container.streams = [mock_video_stream]
        mock_container.demux.return_value = []

        with patch("app.services.camera.av.open", return_value=mock_container):
            cam._capture_stream()

        original_flags.__ior__.assert_called_once_with(Flags.low_delay)

    def test_capture_stream_passes_timeout_to_av_open(self) -> None:
        cam, _ = _make_camera()
        cam._running = True

        mock_video_stream = MagicMock()
        mock_video_stream.type = "video"
        mock_video_stream.codec_context = MagicMock()

        mock_container = MagicMock()
        mock_container.streams = [mock_video_stream]
        mock_container.demux.return_value = []

        with patch(
            "app.services.camera.av.open", return_value=mock_container
        ) as mock_open:
            cam._capture_stream()

        from app.services.camera import _OPEN_TIMEOUT_SECONDS, _STALL_TIMEOUT_SECONDS

        assert mock_open.call_args.kwargs["timeout"] == (
            _OPEN_TIMEOUT_SECONDS,
            _STALL_TIMEOUT_SECONDS,
        )

    def test_inner_loop_breaks_when_stopped_mid_decode(self) -> None:
        """A container that stops running mid-packet must not decode further frames."""
        cam, _ = _make_camera()
        cam._running = True

        mock_frame1 = make_mock_av_frame()
        mock_frame2 = make_mock_av_frame()
        mock_packet = MagicMock()
        mock_packet.decode.return_value = [mock_frame1, mock_frame2]

        mock_video_stream = MagicMock()
        mock_video_stream.type = "video"
        mock_video_stream.codec_context = MagicMock()

        mock_container = MagicMock()
        mock_container.streams = [mock_video_stream]
        mock_container.demux.return_value = [mock_packet]

        decoded_frames: list[object] = []

        def fake_decode(av_frame: object, sequence: int) -> object:
            decoded_frames.append(av_frame)
            cam._running = False
            return make_frame(sequence)

        with (
            patch("app.services.camera.av.open", return_value=mock_container),
            patch("app.services.camera.decode_video_frame", side_effect=fake_decode),
        ):
            cam._capture_stream()

        assert decoded_frames == [mock_frame1]

    def test_outer_loop_breaks_when_stopped_between_packets(self) -> None:
        """Once stopped, a pending second packet must never be decoded."""
        cam, _ = _make_camera()
        cam._running = True

        mock_frame = make_mock_av_frame()
        mock_packet1 = MagicMock()
        mock_packet1.decode.return_value = [mock_frame]
        mock_packet2 = MagicMock()
        mock_packet2.decode.return_value = [mock_frame]

        mock_video_stream = MagicMock()
        mock_video_stream.type = "video"
        mock_video_stream.codec_context = MagicMock()

        mock_container = MagicMock()
        mock_container.streams = [mock_video_stream]
        mock_container.demux.return_value = [mock_packet1, mock_packet2]

        def fake_decode(av_frame: object, sequence: int) -> object:
            cam._running = False
            return make_frame(sequence)

        with (
            patch("app.services.camera.av.open", return_value=mock_container),
            patch("app.services.camera.decode_video_frame", side_effect=fake_decode),
        ):
            cam._capture_stream()

        mock_packet2.decode.assert_not_called()


class TestCloseContainer:
    def test_close_container_idempotent_when_already_none(self) -> None:
        cam, _ = _make_camera()
        cam._container = None
        cam._close_container()  # should not raise
        assert cam._container is None

    def test_close_container_tolerates_close_exception(self) -> None:
        cam, _ = _make_camera()
        mock_container = MagicMock()
        mock_container.close.side_effect = RuntimeError("close failed")
        cam._container = mock_container
        cam._close_container()  # should not raise
        assert cam._container is None
        mock_container.close.assert_called_once()


class TestRTSPOptions:
    def test_low_latency_options_present(self) -> None:
        from app.services.camera import _RTSP_OPTIONS

        assert _RTSP_OPTIONS["analyzeduration"] == "0"
        assert _RTSP_OPTIONS["probesize"] == "32768"
        assert _RTSP_OPTIONS["max_delay"] == "0"
        assert _RTSP_OPTIONS["reorder_queue_size"] == "0"

    def test_tcp_transport_present(self) -> None:
        from app.services.camera import _RTSP_OPTIONS

        assert _RTSP_OPTIONS["rtsp_transport"] == "tcp"

    def test_nobuffer_present(self) -> None:
        from app.services.camera import _RTSP_OPTIONS

        assert _RTSP_OPTIONS["fflags"] == "nobuffer"


class TestRedactUrl:
    def test_removes_user_and_password(self) -> None:
        assert (
            redact_url("rtsp://admin:s3cret@10.0.0.5:554/h264/ch1/main/av_stream")
            == "rtsp://***@10.0.0.5:554/h264/ch1/main/av_stream"
        )

    def test_url_without_credentials_is_unchanged(self) -> None:
        assert redact_url("rtsp://10.0.0.5/stream") == "rtsp://10.0.0.5/stream"

    def test_unparseable_url_is_fully_masked(self) -> None:
        assert redact_url("rtsp://admin:pw@host:notaport/x") == "<unparseable url>"


class TestCredentialsNeverLogged:
    _URL = "rtsp://admin:s3cret@10.0.0.5:554/stream"

    def test_capture_error_log_redacts_url_in_exception_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cam, _ = _make_camera(self._URL)
        cam._running = True

        def fail_then_stop() -> None:
            cam._running = False
            raise ConnectionError(f"Connection refused: '{self._URL}'")

        with (
            caplog.at_level(logging.DEBUG, logger="app.services.camera"),
            patch.object(cam, "_capture_stream", side_effect=fail_then_stop),
        ):
            cam._capture_loop()

        assert "rtsp://***@10.0.0.5:554/stream" in caplog.text
        assert "s3cret" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    def test_stream_opened_log_redacts_url(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cam, _ = _make_camera(self._URL)
        cam._running = True
        mock_container = MagicMock()
        mock_container.streams = [MagicMock(type="video", codec_context=MagicMock())]
        mock_container.demux.return_value = []

        with (
            caplog.at_level(logging.DEBUG, logger="app.services.camera"),
            patch("app.services.camera.av.open", return_value=mock_container),
        ):
            cam._capture_stream()

        assert "RTSP stream opened" in caplog.text
        assert "s3cret" not in caplog.text


class TestReconnectDelay:
    @pytest.mark.parametrize(
        ("attempt", "expected"), [(0, 2.0), (1, 4.0), (2, 8.0), (4, 32.0)]
    )
    def test_doubles_per_attempt_without_jitter(
        self, attempt: int, expected: float
    ) -> None:
        assert reconnect_delay(attempt, 0.5) == pytest.approx(expected)

    def test_is_capped_at_sixty_seconds(self) -> None:
        assert reconnect_delay(5, 0.5) == pytest.approx(60.0)
        assert reconnect_delay(10_000, 0.5) == pytest.approx(60.0)

    def test_jitter_stays_within_twenty_percent(self) -> None:
        assert reconnect_delay(0, 0.0) == pytest.approx(1.6)
        assert reconnect_delay(0, 0.999999) == pytest.approx(2.4, rel=1e-5)


class TestReconnectLoop:
    def _run_loop(self, cam: RTSPCamera, outcomes: list[bool]) -> list[float]:
        """Run _capture_loop over scripted connections; True = got a frame.

        Returns the delays the loop waited between attempts.
        """
        remaining = list(outcomes)
        delays: list[float] = []

        def fake_capture() -> None:
            got_frame = remaining.pop(0)
            if not remaining:
                cam._running = False
            cam._got_frame = got_frame
            raise ConnectionError("down")

        def fake_wait(timeout: float) -> bool:
            delays.append(timeout)
            return False

        cam._running = True
        with (
            patch.object(cam, "_capture_stream", side_effect=fake_capture),
            patch.object(cam._stop_event, "wait", side_effect=fake_wait),
            patch("app.services.camera.random.random", return_value=0.5),
        ):
            cam._capture_loop()
        return delays

    def test_backs_off_exponentially_on_consecutive_failures(self) -> None:
        cam, _ = _make_camera()
        delays = self._run_loop(cam, [False, False, False, False])
        assert delays == pytest.approx([2.0, 4.0, 8.0])

    def test_backoff_resets_after_a_connection_that_delivered_frames(self) -> None:
        cam, _ = _make_camera()
        delays = self._run_loop(cam, [False, False, True, False, False])
        assert delays == pytest.approx([2.0, 4.0, 2.0, 4.0])

    def test_stop_interrupts_a_backoff_wait_immediately(self) -> None:
        cam, _ = _make_camera()
        with (
            patch("app.services.camera.av.open", side_effect=ConnectionError("x")),
            patch("app.services.camera.reconnect_delay", return_value=30.0),
        ):
            cam.start()
            time.sleep(0.05)  # let the thread fail once and start waiting
            started = time.monotonic()
            cam.stop()
            elapsed = time.monotonic() - started

        assert elapsed < 1.0
        assert cam._thread is None


class TestThreadName:
    def test_capture_thread_is_named_after_the_camera(self) -> None:
        buf = FrameBuffer()
        cam = RTSPCamera(rtsp_url="rtsp://fake/stream", buffer=buf, name="porch")
        with (
            patch("app.services.camera.av.open", side_effect=ConnectionError("x")),
            patch("app.services.camera.reconnect_delay", return_value=30.0),
        ):
            cam.start()
            assert cam._thread is not None
            assert cam._thread.name == "rtsp:porch"
            cam.stop()
