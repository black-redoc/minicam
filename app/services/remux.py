"""Copies encoded packets into an output container without re-encoding."""

from __future__ import annotations

import av
from av.container import OutputContainer
from av.video.stream import VideoStream

from app.services.recording.packets import EncodedPacket

# MP4 sample entry per codec, where FFmpeg's default isn't one every browser
# plays: Safari refuses HEVC tagged "hev1" and only plays "hvc1".
_MP4_CODEC_TAGS = {"hevc": "hvc1"}


def add_stream_copy(output: OutputContainer, template: VideoStream) -> None:
    """Add a video stream copying template's codec, tagged so browsers can
    play it. template's input container must still be open."""
    stream = output.add_stream_from_template(template)
    tag = _MP4_CODEC_TAGS.get(template.codec_context.name)
    if tag is not None:
        stream.codec_context.codec_tag = tag


class Remuxer:
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
