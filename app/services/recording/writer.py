"""Writes one clip's packets to an MP4 file on a dedicated thread.

Packets are copied into the MP4 as they are (remux, no re-encoding), which
costs almost no CPU: a Raspberry Pi 5 has no hardware video encoder, so
re-encoding would compete with live streaming and detection for the CPU.
Whatever codec the camera sends (H.264 or HEVC) ends up in the clip.

The file is written as "<name>.mp4.part" and renamed to "<name>.mp4" only
once complete, so a crash or a failed clip never leaves a playable-looking
but truncated file behind.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import av
from av.container import OutputContainer
from av.video.stream import VideoStream

from app.services.av_io import av_io_lock
from app.services.recording.packets import ClipStart, EncodedPacket

logger = logging.getLogger(__name__)

PART_SUFFIX = ".part"
# ~80 s of video at 25 fps: if the disk stalls for longer than that, the clip
# is abandoned rather than letting the queue grow without bound.
_MAX_QUEUED_PACKETS = 2000
_FINISH = object()
# MP4 sample entry per codec, where FFmpeg's default isn't one every browser
# plays: Safari refuses HEVC tagged "hev1" and only plays "hvc1".
_MP4_CODEC_TAGS = {"hevc": "hvc1"}


@dataclass(frozen=True, slots=True)
class FinishedClip:
    camera_name: str
    path: Path
    started_at: datetime
    duration_seconds: float
    size_bytes: int


def open_output(path: Path, template: VideoStream) -> OutputContainer:
    """Open an MP4 output whose video stream copies template's codec,
    tagged so browsers can play it.

    Must be called while template's input container is open, with
    av_io_lock held (see RTSPCamera._open_clip_output).
    """
    # faststart moves the index to the front when the clip is closed, so a
    # browser can start playing before the whole file has downloaded.
    output = av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"})
    try:
        stream = output.add_stream_from_template(template)
        tag = _MP4_CODEC_TAGS.get(template.codec_context.name)
        if tag is not None:
            stream.codec_context.codec_tag = tag
    except BaseException:
        output.close()
        raise
    return output


class ClipWriter:
    """A ClipSink that muxes packets into one MP4 file on its own thread."""

    def __init__(
        self,
        output: OutputContainer,
        start: ClipStart,
        path: Path,
        on_finished: Callable[[FinishedClip], None],
        max_queued_packets: int = _MAX_QUEUED_PACKETS,
    ) -> None:
        self._output = output
        self._start = start
        self._path = path
        self._part_path = path.with_name(path.name + PART_SUFFIX)
        self._on_finished = on_finished
        self._max_queued_packets = max_queued_packets
        # Unbounded so finish() can always enqueue its marker; write()
        # enforces the packet bound itself.
        self._queue: queue.Queue[object] = queue.Queue()
        self._overflowed = False
        self._thread = threading.Thread(
            target=self._run, name=f"clip:{start.camera_name}", daemon=True
        )

    @classmethod
    def open(
        cls,
        start: ClipStart,
        path: Path,
        on_finished: Callable[[FinishedClip], None],
    ) -> ClipWriter | None:
        """Open the clip's output and start its thread, or None if the
        camera's input stream is already gone."""
        part_path = path.with_name(path.name + PART_SUFFIX)
        output = start.opener(part_path)
        if output is None:
            return None
        writer = cls(output, start, path, on_finished)
        writer._thread.start()
        return writer

    @property
    def part_path(self) -> Path:
        return self._part_path

    def write(self, packet: EncodedPacket) -> None:
        if self._overflowed:
            return
        if self._queue.qsize() >= self._max_queued_packets:
            self._overflowed = True
            logger.error(
                "Recording for camera %r fell behind (disk too slow?); "
                "abandoning this clip",
                self._start.camera_name,
            )
            return
        self._queue.put(packet)

    def finish(self) -> None:
        self._queue.put(_FINISH)

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the thread to finish; True if it did."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        muxer = _Muxer(self._output)
        failed = False
        while (item := self._queue.get()) is not _FINISH:
            if failed or self._overflowed:
                continue
            assert isinstance(item, EncodedPacket)
            try:
                muxer.mux(item)
            except Exception as exc:
                failed = True
                logger.error(
                    "Recording for camera %r failed: %s: %s",
                    self._start.camera_name,
                    type(exc).__name__,
                    exc,
                )
        try:
            with av_io_lock:
                self._output.close()
        except Exception as exc:
            failed = True
            logger.error(
                "Could not finalize recording for camera %r: %s: %s",
                self._start.camera_name,
                type(exc).__name__,
                exc,
            )

        if failed or self._overflowed or muxer.count == 0:
            self._part_path.unlink(missing_ok=True)
            return
        try:
            self._part_path.rename(self._path)
            size_bytes = self._path.stat().st_size
        except OSError as exc:
            logger.error(
                "Could not save recording for camera %r: %s",
                self._start.camera_name,
                exc,
            )
            self._part_path.unlink(missing_ok=True)
            return
        self._on_finished(
            FinishedClip(
                camera_name=self._start.camera_name,
                path=self._path,
                started_at=self._start.started_at,
                duration_seconds=muxer.duration_seconds,
                size_bytes=size_bytes,
            )
        )


class _Muxer:
    """Rebases timestamps to start at zero and keeps DTS strictly increasing.

    MP4 rejects a packet whose DTS doesn't increase, which would abort the
    whole clip. Cameras occasionally send a repeated or missing timestamp,
    so such a packet is nudged forward by one tick instead.
    """

    def __init__(self, output: OutputContainer) -> None:
        self._output = output
        self._stream = output.streams.video[0]
        self._origin: int | None = None
        self._last_dts: int | None = None
        self._first_pts: int | None = None
        self._max_pts: int | None = None
        self._time_base = 0.0
        self.count = 0

    @property
    def duration_seconds(self) -> float:
        if self._first_pts is None or self._max_pts is None:
            return 0.0
        return (self._max_pts - self._first_pts) * self._time_base

    def mux(self, packet: EncodedPacket) -> None:
        dts = packet.dts if packet.dts is not None else packet.pts
        if dts is None:
            return  # no timestamp at all: can't be placed in the file
        if self._origin is None:
            self._origin = dts
            self._time_base = float(packet.time_base)
        dts -= self._origin
        if self._last_dts is not None and dts <= self._last_dts:
            dts = self._last_dts + 1
        pts = packet.pts - self._origin if packet.pts is not None else dts
        pts = max(pts, dts)

        out = av.Packet(packet.data)
        out.dts = dts
        out.pts = pts
        out.time_base = packet.time_base
        out.is_keyframe = packet.is_keyframe
        out.stream = self._stream
        self._output.mux(out)

        self._last_dts = dts
        self._first_pts = pts if self._first_pts is None else min(self._first_pts, pts)
        self._max_pts = pts if self._max_pts is None else max(self._max_pts, pts)
        self.count += 1
