from __future__ import annotations

import logging
import random
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import av
from av.codec.context import Flags
from av.container import OutputContainer

from app.buffers.frame_buffer import FrameBuffer
from app.services.av_io import av_io_lock
from app.services.decoder import decode_video_frame
from app.services.recording.packets import EncodedPacket
from app.services.recording.recorder import CameraRecorder
from app.services.recording.writer import open_output

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
# Reconnect backoff: 2s, 4s, 8s ... capped at 60s, each with +/-20% jitter so
# several cameras behind one dead switch do not retry in lockstep. Reset once
# a connection actually delivers a frame.
_RECONNECT_BASE_DELAY_SECONDS = 2.0
_RECONNECT_MAX_DELAY_SECONDS = 60.0
_RECONNECT_JITTER = 0.2
_OPEN_TIMEOUT_SECONDS = 5.0
_STALL_TIMEOUT_SECONDS = 15.0
# Longer than the open and stall timeouts: a capture thread blocked on a
# dead camera always gets to the end of its read and exits within this.
_STOP_TIMEOUT_SECONDS = 20.0

# Shared across every RTSPCamera instance/thread and the clip writers: see
# app/services/av_io.py for why container open/close must be serialized.
_av_io_lock = av_io_lock


def reconnect_delay(attempt: int, rand: float) -> float:
    """Backoff for the attempt-th consecutive failure (0-based).

    Args:
        attempt: Consecutive failed connections so far, starting at 0.
        rand: A uniform sample in [0, 1) that drives the jitter.
    """
    base = min(
        _RECONNECT_MAX_DELAY_SECONDS,
        _RECONNECT_BASE_DELAY_SECONDS * 2.0 ** min(attempt, 16),
    )
    return base * (1 + _RECONNECT_JITTER * (2 * rand - 1))


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
        name: Camera name, used for the capture thread's name.
        recorder: Receives every demuxed packet for event recording, if any.
    """

    def __init__(
        self,
        rtsp_url: str,
        buffer: FrameBuffer,
        name: str = "camera",
        recorder: CameraRecorder | None = None,
    ) -> None:
        self._name = name
        self._rtsp_url = rtsp_url
        # The full URL embeds camera credentials: only this redacted form
        # may ever reach a log line.
        self._log_url = redact_url(rtsp_url)
        self._buffer = buffer
        self._recorder = recorder
        self._running = False
        self._connected = False
        self._sequence = 0
        self._container: av.container.InputContainer | None = None
        self._thread: threading.Thread | None = None
        self._container_lock = threading.Lock()
        # Set by stop() so a backoff wait ends immediately instead of
        # outliving the join timeout.
        self._stop_event = threading.Event()
        self._got_frame = False

    def start(self) -> None:
        """Start the RTSP capture loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"rtsp:{self._name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the capture loop and release all resources.

        Never closes the container while the capture thread may still be
        demuxing it: FFmpeg frees the container's memory on close, so a
        close from this thread during a demux is a use-after-free that
        segfaults the whole process. Instead the capture thread sees
        _running is False at its next packet (or when its read times out)
        and closes the container itself.
        """
        self._running = False
        self._stop_event.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            if thread.is_alive():
                # Still inside FFmpeg: leave the close to its own finally.
                logger.warning(
                    "Capture thread did not stop within %.1f seconds",
                    _STOP_TIMEOUT_SECONDS,
                )
                self._thread = None
                self._connected = False
                return
            self._thread = None

        self._close_container()
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
        an exponential backoff delay, so reconnects are never silent or
        immediate. The backoff resets once a connection delivers a frame.
        """
        attempt = 0
        while self._running:
            self._got_frame = False
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
            if self._got_frame:
                attempt = 0
            if self._running:
                delay = reconnect_delay(attempt, random.random())
                attempt += 1
                logger.warning(
                    "Reconnecting to %s in %.1fs (attempt %d)",
                    self._log_url,
                    delay,
                    attempt,
                )
                self._stop_event.wait(delay)

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

        recorder = self._recorder
        time_base = cast(Fraction, video_stream.time_base)
        if recorder is not None:
            recorder.on_stream_opened(self._open_clip_output)
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
                    self._got_frame = True
                if recorder is not None:
                    encoded = EncodedPacket.from_av(packet, time_base, time.monotonic())
                    if encoded is not None:
                        recorder.on_packet(encoded)
        finally:
            if recorder is not None:
                recorder.on_stream_closed()
            self._close_container()
            self._connected = False

    def _open_clip_output(self, path: Path) -> OutputContainer | None:
        """Open an MP4 output copying the live video stream's codec, or None
        if the input is already closed or the camera is stopping.

        Called from the capture thread, the only one that closes the input
        container while it runs (see stop()).
        """
        with self._container_lock, _av_io_lock:
            container = self._container
            if container is None or not self._running:
                return None
            return open_output(path, container.streams.video[0])
