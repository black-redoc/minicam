"""One camera's live fMP4 streams, shared by every viewer of that camera."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable

from app.services.live.fmp4 import Fmp4Segmenter, Fragment, InitSegment
from app.services.recording.packets import EncodedPacket

logger = logging.getLogger(__name__)

SegmenterOpener = Callable[[], Fmp4Segmenter | None]
LiveMessage = InitSegment | Fragment

# A GOP longer than this (~60 s at 10 fps) means keyframes stopped coming:
# new viewers then wait for the next keyframe instead of getting all of it.
_MAX_GOP_FRAGMENTS = 600
# ~8 s of a 4 Mbit/s main stream: a viewer further behind than this skips
# ahead to the next keyframe instead of falling further behind.
_MAX_QUEUED_BYTES = 4 * 1024 * 1024
# A rendition keeps remuxing this long after its last viewer leaves (or
# after the stream opens, which is when its MIME type becomes known), so a
# viewer that reconnects still starts at once from the buffered GOP.
_IDLE_STOP_SECONDS = 10.0


class LiveSubscription:
    """One viewer's queue of messages, filled on the event loop."""

    def __init__(self, rendition: _Rendition, max_queued_bytes: int) -> None:
        self._rendition = rendition
        self._max_queued_bytes = max_queued_bytes
        self._queue: deque[LiveMessage] = deque()
        self._queued_bytes = 0
        self._skipping = False
        self._ready = asyncio.Event()

    def __enter__(self) -> LiveSubscription:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._rendition.unsubscribe(self)

    async def next(self) -> LiveMessage:
        """Wait for and return the next message."""
        while not self._queue:
            self._ready.clear()
            await self._ready.wait()
        message = self._queue.popleft()
        self._queued_bytes -= len(message.data)
        return message

    def push(self, message: LiveMessage) -> None:
        """Queue message. Only called on the event loop."""
        if isinstance(message, InitSegment):
            # A new stream: whatever is queued belongs to the old one.
            self._queue.clear()
            self._queued_bytes = 0
            self._skipping = False
        elif self._skipping and not message.is_keyframe:
            return
        elif self._queued_bytes + len(message.data) > self._max_queued_bytes:
            # Drop the backlog, keeping any init segment it holds: a
            # compressed stream can only resume at a keyframe.
            self._queue = deque(m for m in self._queue if isinstance(m, InitSegment))
            self._queued_bytes = sum(len(m.data) for m in self._queue)
            self._skipping = not message.is_keyframe
            if self._skipping:
                return
        else:
            self._skipping = False
        self._queue.append(message)
        self._queued_bytes += len(message.data)
        self._ready.set()


class _Rendition:
    """One fMP4 stream of a camera (video only, or video with audio)."""

    def __init__(self, max_queued_bytes: int) -> None:
        self._max_queued_bytes = max_queued_bytes
        # Guards everything a viewer reads when subscribing.
        self._lock = threading.Lock()
        self._init: InitSegment | None = None
        self._gop: list[Fragment] = []
        self._subscribers: set[LiveSubscription] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Kept while the segmenter is stopped for lack of viewers.
        self._mime: str | None = None
        # Capture thread only.
        self._opener: SegmenterOpener | None = None
        self._segmenter: Fmp4Segmenter | None = None
        self._idle_since: float | None = None

    @property
    def mime(self) -> str | None:
        return self._mime

    def subscribe(self) -> LiveSubscription:
        subscription = LiveSubscription(self, self._max_queued_bytes)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            if self._init is not None:
                subscription.push(self._init)
                for fragment in self._gop:
                    subscription.push(fragment)
            self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: LiveSubscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def on_stream_opened(self, opener: SegmenterOpener | None) -> None:
        self._opener = opener
        self._idle_since = None

    def on_packet(self, packet: EncodedPacket) -> None:
        if not self._wanted(packet.received_at):
            if self._segmenter is not None:
                self._stop()
            return
        if self._segmenter is None:
            # Fragments must start at a video keyframe to be playable.
            if self._opener is None or not _is_video_keyframe(packet):
                return
            try:
                self._segmenter = self._opener()
            except Exception as exc:
                logger.error("Live stream unavailable: %s: %s", type(exc).__name__, exc)
            if self._segmenter is None:
                self._opener = None  # until the camera reconnects
                return

        segmenter = self._segmenter
        try:
            fragments = segmenter.push(packet)
        except Exception as exc:
            # Until the camera reconnects: viewers fall back to MJPEG.
            logger.error("Live stream stopped: %s: %s", type(exc).__name__, exc)
            self._opener = None
            self._close_segmenter()
            return
        if self._init is None and segmenter.init is not None:
            self._publish(segmenter.init)
        for fragment in fragments:
            self._publish(fragment)

    def on_stream_closed(self) -> None:
        self._opener = None
        self._stop()
        with self._lock:
            self._mime = None

    def _wanted(self, now: float) -> bool:
        """Whether to remux now: with viewers, and until _IDLE_STOP_SECONDS
        after the last one left or the stream opened."""
        with self._lock:
            has_viewers = bool(self._subscribers)
        if has_viewers:
            self._idle_since = None
            return True
        if self._idle_since is None:
            self._idle_since = now
        return now - self._idle_since < _IDLE_STOP_SECONDS

    def _stop(self) -> None:
        """Close the segmenter; a viewer restarts it at the next keyframe."""
        self._close_segmenter()
        with self._lock:
            self._init = None
            self._gop = []

    def _close_segmenter(self) -> None:
        segmenter, self._segmenter = self._segmenter, None
        if segmenter is not None:
            try:
                segmenter.close()
            except Exception as exc:
                logger.warning(
                    "Could not close live stream: %s: %s", type(exc).__name__, exc
                )

    def _publish(self, message: LiveMessage) -> None:
        with self._lock:
            if isinstance(message, InitSegment):
                self._init = message
                self._mime = message.mime
                self._gop = []
            elif message.is_keyframe:
                self._gop = [message]
            elif self._gop and len(self._gop) < _MAX_GOP_FRAGMENTS:
                self._gop.append(message)
            else:
                self._gop = []
            subscribers = list(self._subscribers)
            loop = self._loop
        if loop is None:
            return
        for subscription in subscribers:
            try:
                loop.call_soon_threadsafe(subscription.push, message)
            except RuntimeError:
                return  # loop closed (shutdown): nobody left to deliver to


def _is_video_keyframe(packet: EncodedPacket) -> bool:
    return packet.kind == "video" and packet.is_keyframe


class LiveFeed:
    """Remuxes one camera's packets into fMP4 and fans them out to viewers.

    Two renditions run side by side while the camera is connected: video
    only, for everyone, and video with audio, sent only to viewers who asked
    for it (and may hear it, which the stream router checks). Audio packets
    must already be on the video's clock (see AudioClockAligner).

    The capture thread calls on_stream_opened/on_packet/on_stream_closed;
    viewers subscribe on the event loop. A new viewer starts with the init
    segment and the fragments since the latest keyframe, so playback can
    begin immediately instead of waiting for the next keyframe.

    Each rendition only remuxes while it has viewers, plus _IDLE_STOP_SECONDS
    after the last one leaves or the stream opens (long enough to learn its
    MIME type, which stays known until the camera disconnects). A viewer of
    a stopped rendition gets its init segment at the next video keyframe.
    """

    def __init__(self, max_queued_bytes: int = _MAX_QUEUED_BYTES) -> None:
        self._video = _Rendition(max_queued_bytes)
        self._with_audio = _Rendition(max_queued_bytes)

    @property
    def mime(self) -> str | None:
        """MIME type (with codecs) of the video-only stream, if known yet."""
        return self._video.mime

    @property
    def audio_mime(self) -> str | None:
        """MIME type (with codecs) of the video-with-audio stream, or None
        while it isn't known or the camera sends no supported audio."""
        return self._with_audio.mime

    # ── Viewers (event loop) ────────────────────────────────────────────

    def subscribe(self, audio: bool = False) -> LiveSubscription:
        return (self._with_audio if audio else self._video).subscribe()

    # ── Capture thread ──────────────────────────────────────────────────

    def on_stream_opened(
        self, opener: SegmenterOpener, audio_opener: SegmenterOpener | None = None
    ) -> None:
        """opener builds the video-only segmenter; audio_opener, if the
        camera has audio, the one with audio (it may still return None)."""
        self._video.on_stream_opened(opener)
        self._with_audio.on_stream_opened(audio_opener)

    def on_packet(self, packet: EncodedPacket) -> None:
        if packet.kind == "video":
            self._video.on_packet(packet)
        self._with_audio.on_packet(packet)

    def on_stream_closed(self) -> None:
        self._video.on_stream_closed()
        self._with_audio.on_stream_closed()
