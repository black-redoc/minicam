from __future__ import annotations

import logging
import threading
import time
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import av
from av.codec.context import Flags

from app.buffers.frame_buffer import FrameBuffer
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
_OPEN_TIMEOUT_SECONDS = 5.0
_STALL_TIMEOUT_SECONDS = 15.0

# Shared across every RTSPCamera instance/thread: PyAV/FFmpeg's container
# open/close is not thread-safe even across unrelated streams — concurrent
# av.open()/close() calls from different capture threads corrupted the
# process heap in production. Demux/decode of an already-open container is
# unaffected and still runs fully in parallel; only the open/close sequence
# is serialized here.
_av_io_lock = threading.Lock()


def redact_url(url: str) -> str:
    """Return url with any userinfo (user:password@) removed, safe to log."""
    try:
        parts = urlsplit(url)
        netloc = parts.hostname or ""
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
    except ValueError:
        return "<unparseable url>"
    if parts.username is None and parts.password is None:
        return url
    return urlunsplit(parts._replace(netloc=f"***@{netloc}"))


class RTSPCamera:
    """RTSP stream reader that pushes decoded frames to a FrameBuffer.

    Single producer: only the internal background thread writes to the buffer.
    Uses ThreadPoolExecutor to avoid raw threading.Thread management.

    Args:
        rtsp_url: Full RTSP URL including credentials.
        buffer: The FrameBuffer to push frames into.
    """

    def __init__(self, rtsp_url: str, buffer: FrameBuffer) -> None:
        self._rtsp_url = rtsp_url
        # The full URL embeds camera credentials: only this redacted form
        # may ever reach a log line.
        self._log_url = redact_url(rtsp_url)
        self._buffer = buffer
        self._running = False
        self._connected = False
        self._sequence = 0
        self._container: av.container.InputContainer | None = None
        self._thread: threading.Thread | None = None
        self._container_lock = threading.Lock()

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
                with _av_io_lock:
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
            except Exception as exc:
                # No exc_info: PyAV/FFmpeg errors embed the full URL (with
                # credentials) in their message, so a traceback would leak it.
                logger.error(
                    "RTSP capture error on %s: %s: %s",
                    self._log_url,
                    type(exc).__name__,
                    str(exc).replace(self._rtsp_url, self._log_url),
                )
            else:
                if self._running:
                    logger.warning(
                        "RTSP stream for %s ended without an error "
                        "(demuxer stopped yielding packets)",
                        self._log_url,
                    )
            self._connected = False
            if self._running:
                logger.warning(
                    "Reconnecting to %s in %.1fs",
                    self._log_url,
                    _RECONNECT_DELAY_SECONDS,
                )
                time.sleep(_RECONNECT_DELAY_SECONDS)

    def _capture_stream(self) -> None:
        """Open the RTSP stream and decode frames until disconnection."""
        if not self._running:
            return

        with self._container_lock, _av_io_lock:
            self._container = av.open(
                self._rtsp_url,
                options=_RTSP_OPTIONS,
                timeout=(_OPEN_TIMEOUT_SECONDS, _STALL_TIMEOUT_SECONDS),
            )
            video_stream = next(s for s in self._container.streams if s.type == "video")
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

        try:
            self._connected = True
            logger.info("RTSP stream opened: %s", self._log_url)

            for packet in self._container.demux(video_stream):
                if not self._running:
                    break
                for av_frame in packet.decode():
                    if not self._running:
                        break
                    frame = decode_video_frame(
                        cast(av.VideoFrame, av_frame), self._sequence
                    )
                    self._sequence += 1
                    self._buffer.write(frame)
        finally:
            self._close_container()
            self._connected = False
