import time
from unittest.mock import MagicMock, patch

import pytest
from av.codec.context import Flags

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.services.camera import RTSPCamera
from tests.fixtures.camera import make_mock_av_frame


def _make_camera(url: str = "rtsp://fake/stream") -> tuple[RTSPCamera, DoubleFrameBuffer]:
    buf = DoubleFrameBuffer()
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
        with patch("app.services.camera.av.open", side_effect=ConnectionError("no cam")):
            cam.start()
            assert cam._running is True
            cam.stop()

    def test_stop_sets_running_false(self) -> None:
        cam, _ = _make_camera()
        with patch("app.services.camera.av.open", side_effect=ConnectionError("no cam")):
            cam.start()
            cam.stop()
            assert cam._running is False

    def test_double_start_does_not_raise(self) -> None:
        cam, _ = _make_camera()
        with patch("app.services.camera.av.open", side_effect=ConnectionError("no cam")):
            cam.start()
            cam.start()
            cam.stop()

    def test_stop_without_start_does_not_raise(self) -> None:
        cam, _ = _make_camera()
        cam.stop()

    def test_is_not_connected_after_stop(self) -> None:
        cam, _ = _make_camera()
        with patch("app.services.camera.av.open", side_effect=ConnectionError("no cam")):
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
