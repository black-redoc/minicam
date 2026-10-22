"""Per-camera event recorder: pre-roll buffer and clip start/stop decisions.

Two threads touch a CameraRecorder, through separate methods:

- The camera's capture thread calls on_stream_opened(), on_packet() and
  on_stream_closed(). Every clip decision happens here, on packet arrival,
  so clips always start on a keyframe and no timer is needed.
- The event loop calls arm() and record_until() from detection results.
  Both only replace a single attribute, which is atomic under the GIL, so
  no lock is needed (same reasoning as FrameBuffer.write()).

The pre-roll buffer is kept as a list of GOPs (a keyframe and the packets
that follow it), so a clip can always start at a keyframe a few seconds
before the detection that triggered it.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.services.recording.packets import (
    ClipSink,
    ClipSinkFactory,
    ClipStart,
    EncodedPacket,
    OutputOpener,
)

PRE_ROLL_SECONDS = 3.0
MAX_CLIP_SECONDS = 300.0
# Upper bound on buffered packets (~60 s at 25 fps), for a camera whose
# keyframes are so far apart that the pre-roll would otherwise grow without
# limit.
_MAX_BUFFERED_PACKETS = 1500


class CameraRecorder:
    """Turns a camera's packet stream into clips while recording is wanted."""

    def __init__(
        self,
        camera_name: str,
        open_clip: ClipSinkFactory,
        pre_roll_seconds: float = PRE_ROLL_SECONDS,
        max_clip_seconds: float = MAX_CLIP_SECONDS,
        max_buffered_packets: int = _MAX_BUFFERED_PACKETS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._camera_name = camera_name
        self._open_clip = open_clip
        self._pre_roll_seconds = pre_roll_seconds
        self._max_clip_seconds = max_clip_seconds
        self._max_buffered_packets = max_buffered_packets
        self._now = now
        # Written by the event loop, read by the capture thread.
        self._armed = False
        self._record_until = -math.inf
        # Capture-thread state only.
        self._opener: OutputOpener | None = None
        self._gops: deque[list[EncodedPacket]] = deque()
        self._buffered = 0
        self._sink: ClipSink | None = None
        self._clip_started_at = 0.0
        # After a refused clip (e.g. disk full), retry on the next keyframe
        # only, not on every packet.
        self._open_refused = False

    # ── Event loop side ─────────────────────────────────────────────────

    def arm(self, armed: bool) -> None:
        """Keep a pre-roll buffer (armed) or drop it and stop any clip."""
        self._armed = armed

    def record_until(self, deadline: float) -> None:
        """Record until the time.monotonic() deadline; may be extended."""
        self._record_until = deadline

    # ── Capture thread side ─────────────────────────────────────────────

    def on_stream_opened(self, opener: OutputOpener) -> None:
        """A new input stream started: older packets can't be continued."""
        self._stop_clip()
        self._clear_buffer()
        self._opener = opener

    def on_stream_closed(self) -> None:
        self._stop_clip()
        self._clear_buffer()
        self._opener = None

    def on_packet(self, packet: EncodedPacket) -> None:
        if not self._armed:
            self._stop_clip()
            self._clear_buffer()
            return

        now = packet.received_at
        wanted = now < self._record_until
        if self._sink is not None:
            if not wanted:
                self._stop_clip()
            elif (
                packet.is_keyframe
                and now - self._clip_started_at >= self._max_clip_seconds
            ):
                # Roll over to a new clip on this keyframe: a long event is
                # split into several clips with no footage lost in between.
                self._stop_clip()
                self._clear_buffer()
            else:
                self._sink.write(packet)
                return

        self._buffer(packet)
        if wanted and (packet.is_keyframe or not self._open_refused):
            self._start_clip(now)

    def _buffer(self, packet: EncodedPacket) -> None:
        if packet.is_keyframe:
            self._gops.append([packet])
        elif self._gops:
            self._gops[-1].append(packet)
        else:
            return  # nothing decodable before the first keyframe
        self._buffered += 1
        now = packet.received_at
        while len(self._gops) >= 2 and (
            self._gops[1][0].received_at <= now - self._pre_roll_seconds
            or self._buffered > self._max_buffered_packets
        ):
            self._buffered -= len(self._gops.popleft())
        if self._buffered > self._max_buffered_packets:
            self._clear_buffer()

    def _clear_buffer(self) -> None:
        self._gops.clear()
        self._buffered = 0

    def _start_clip(self, now: float) -> None:
        if not self._gops or self._opener is None:
            return
        first = self._gops[0][0]
        started_at = self._now() - timedelta(seconds=now - first.received_at)
        sink = self._open_clip(ClipStart(self._camera_name, started_at, self._opener))
        self._open_refused = sink is None
        if sink is None:
            return
        for gop in self._gops:
            for packet in gop:
                sink.write(packet)
        self._sink = sink
        self._clip_started_at = first.received_at
        # These packets now belong to this clip: never replay them as the
        # pre-roll of the next one.
        self._clear_buffer()

    def _stop_clip(self) -> None:
        sink = self._sink
        if sink is not None:
            self._sink = None
            sink.finish()
