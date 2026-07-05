from __future__ import annotations

import logging
import threading
import time

import av  # type: ignore[import-untyped]
from av.codec.context import Flags

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.services.decoder import decode_video_frame

logger = logging.getLogger(__name__)

_RTSP_OPTIONS: dict[str, str] = {
    "rtsp_transport": "tcp",
    "stimeout": "5000000",  # 5 second connection timeout in microseconds
    "fflags": "nobuffer",
    "analyzeduration": "0",
    "probesize": "32768",
    "max_delay": "0",
    "reorder_queue_size": "0",
}
_RECONNECT_DELAY_SECONDS = 2.0
_STOP_TIMEOUT_SECONDS = 10.0
_STALL_TIMEOUT_SECONDS = 15.0
_WATCHDOG_INTERVAL_SECONDS = 3.0


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
        self._thread: threading.Thread | None = None
        self._container_lock = threading.Lock()
        self._last_packet_time = 0.0
        self._watchdog_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the RTSP capture loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="rtsp_camera",
            daemon=True,
        )
        self._thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="rtsp_watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Stop the capture loop and release all resources."""
        self._running = False

        self._close_container()

        if self._thread is not None:
            self._thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            if self._thread.is_alive():
                logger.warning(
                    "Capture thread did not stop within %.1f seconds",
                    _STOP_TIMEOUT_SECONDS,
                )
            self._thread = None

        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            if self._watchdog_thread.is_alive():
                logger.warning(
                    "Watchdog thread did not stop within %.1f seconds",
                    _STOP_TIMEOUT_SECONDS,
                )
            self._watchdog_thread = None

        self._connected = False

    def is_connected(self) -> bool:
        """Return True if currently receiving frames from the camera."""
        return self._connected

    def _close_container(self) -> None:
        """Close and clear the active container, tolerating a concurrent close."""
        with self._container_lock:
            container = self._container
            self._container = None
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

    def _capture_loop(self) -> None:
        """Reconnect on failure until stop() is called.

        _capture_stream() can end two ways: by raising (network/decode error)
        or by returning normally (the demuxer simply stopped yielding packets
        — e.g. the camera closed the RTSP session on its own, no exception
        involved). Both are treated as a disconnect: logged and followed by
        the same backoff delay, so reconnects are never silent or immediate.
        """
        while self._running:
            try:
                self._capture_stream()
            except Exception:
                logger.error(
                    "RTSP capture error on %s", self._rtsp_url, exc_info=True
                )
            else:
                if self._running:
                    logger.warning(
                        "RTSP stream for %s ended without an error "
                        "(demuxer stopped yielding packets)",
                        self._rtsp_url,
                    )
            self._connected = False
            if self._running:
                logger.warning(
                    "Reconnecting to %s in %.1fs",
                    self._rtsp_url,
                    _RECONNECT_DELAY_SECONDS,
                )
                time.sleep(_RECONNECT_DELAY_SECONDS)

    def _capture_stream(self) -> None:
        """Open the RTSP stream and decode frames until disconnection."""
        if not self._running:
            return

        with self._container_lock:
            self._container = av.open(self._rtsp_url, options=_RTSP_OPTIONS)

        try:
            video_stream = next(
                s for s in self._container.streams if s.type == "video"
            )
            video_stream.codec_context.flags |= Flags.low_delay
            # Bound ffmpeg's internal slice-threading. With thread_count=0
            # (auto) it spawns one decode worker per CPU core — all inheriting
            # this thread's "rtsp_camera" name — and on reconnect a fresh pool
            # is created without the old one being torn down synchronously,
            # so the thread count multiplies with every reconnect. A fixed
            # small count keeps decode parallel enough for real-time playback
            # while capping how many threads a single stream can ever hold.
            video_stream.codec_context.thread_type = "SLICE"
            video_stream.codec_context.thread_count = 2
            self._connected = True
            self._last_packet_time = time.monotonic()
            logger.info("RTSP stream opened: %s", self._rtsp_url)

            for packet in self._container.demux(video_stream):
                self._last_packet_time = time.monotonic()
                if not self._running:
                    break
                for av_frame in packet.decode():
                    if not self._running:
                        break
                    frame = decode_video_frame(av_frame, self._sequence)
                    self._sequence += 1
                    self._buffer.write(frame)
        finally:
            self._close_container()
            self._connected = False

    def _watchdog_tick(self) -> None:
        """Force a reconnect if no RTSP packets have arrived recently."""
        if not self._connected:
            return
        stalled_for = time.monotonic() - self._last_packet_time
        if stalled_for > _STALL_TIMEOUT_SECONDS:
            logger.warning(
                "No RTSP packets from %s in %.1fs, forcing reconnect",
                self._rtsp_url,
                stalled_for,
            )
            self._close_container()

    def _watchdog_loop(self) -> None:
        while self._running:
            time.sleep(_WATCHDOG_INTERVAL_SECONDS)
            if self._running:
                self._watchdog_tick()
