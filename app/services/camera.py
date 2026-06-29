from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

import av  # type: ignore[import-untyped]

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.services.decoder import decode_video_frame

logger = logging.getLogger(__name__)

_RTSP_OPTIONS: dict[str, str] = {
    "rtsp_transport": "tcp",
    "stimeout": "5000000",  # 5 second connection timeout in microseconds
}
_RECONNECT_DELAY_SECONDS = 2.0
_STOP_TIMEOUT_SECONDS = 10.0


class RTSPCamera:
    """RTSP stream reader that pushes decoded frames to a DoubleFrameBuffer.

    Single producer: only the internal background thread writes to the buffer.
    Uses ThreadPoolExecutor to avoid raw threading.Thread management.

    Args:
        rtsp_url: Full RTSP URL including credentials.
        buffer: The DoubleFrameBuffer to push frames into.
    """

    def __init__(self, rtsp_url: str, buffer: DoubleFrameBuffer) -> None:
        self._rtsp_url = rtsp_url
        self._buffer = buffer
        self._running = False
        self._connected = False
        self._sequence = 0
        self._container: av.container.InputContainer | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._future: Future[None] | None = None

    def start(self) -> None:
        """Start the RTSP capture loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="rtsp_camera",
        )
        self._future = self._executor.submit(self._capture_loop)

    def stop(self) -> None:
        """Stop the capture loop and release all resources."""
        self._running = False

        container = self._container
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

        if self._future is not None:
            try:
                self._future.result(timeout=_STOP_TIMEOUT_SECONDS)
            except Exception as exc:
                logger.warning("Error waiting for capture thread: %s", exc)

        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

        self._connected = False

    def is_connected(self) -> bool:
        """Return True if currently receiving frames from the camera."""
        return self._connected

    def _capture_loop(self) -> None:
        """Reconnect on failure until stop() is called."""
        while self._running:
            try:
                self._capture_stream()
            except Exception as exc:
                logger.error("RTSP capture error: %s", exc)
                self._connected = False
                if self._running:
                    time.sleep(_RECONNECT_DELAY_SECONDS)

    def _capture_stream(self) -> None:
        """Open the RTSP stream and decode frames until disconnection."""
        if not self._running:
            return

        self._container = av.open(self._rtsp_url, options=_RTSP_OPTIONS)

        try:
            video_stream = next(
                s for s in self._container.streams if s.type == "video"
            )
            video_stream.codec_context.low_delay = 1
            self._connected = True
            logger.info("RTSP stream opened: %s", self._rtsp_url)

            for packet in self._container.demux(video_stream):
                if not self._running:
                    break
                for av_frame in packet.decode():
                    if not self._running:
                        break
                    frame = decode_video_frame(av_frame, self._sequence)
                    self._sequence += 1
                    self._buffer.write(frame)
        finally:
            container = self._container
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
                self._container = None
            self._connected = False
