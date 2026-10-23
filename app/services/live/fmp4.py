"""Fragmented MP4 for live viewing.

The camera's packets are remuxed (never re-encoded) into an init segment
(ftyp + moov) followed by one fragment (moof + mdat) per frame, the format
browsers append to a <video> through Media Source Extensions.
"""

from __future__ import annotations

import io
import struct
from collections import deque
from dataclasses import dataclass

import av
from av.container import OutputContainer
from av.video.stream import VideoStream

from app.services.av_io import av_io_lock
from app.services.recording.packets import EncodedPacket
from app.services.remux import Remuxer, add_stream_copy

# empty_moov: the init segment holds no samples, so it can be sent on its
# own. default_base_moof: each fragment is self-contained, so a viewer can
# start at any keyframe. frag_every_frame: one fragment per frame, so the
# viewer is never a whole GOP behind.
_MOVFLAGS = "frag_keyframe+empty_moov+default_base_moof+frag_every_frame"
_BOX_HEADER = struct.Struct(">I4s")


@dataclass(frozen=True, slots=True)
class InitSegment:
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Fragment:
    data: bytes
    is_keyframe: bool


class _Sink(io.RawIOBase):
    """Collects whatever the muxer writes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        self.buffer += data
        return len(data)


class Fmp4Segmenter:
    """Turns one camera stream's packets into an init segment and fragments.

    FFmpeg writes a frame's fragment only once the next packet arrives (it
    needs that timestamp for the frame's duration), so fragments come out
    one frame behind the packets pushed.
    """

    def __init__(self, output: OutputContainer, sink: _Sink) -> None:
        self._output = output
        self._sink = sink
        self._remuxer = Remuxer(output)
        # Keyframe flags of muxed packets whose fragment isn't out yet.
        self._pending: deque[bool] = deque()
        self._parts: list[tuple[str, bytes]] = []
        self.init: InitSegment | None = None

    @classmethod
    def open(cls, template: VideoStream) -> Fmp4Segmenter:
        """Must be called while template's input container is open, with
        av_io_lock held (see RTSPCamera._open_live_output)."""
        sink = _Sink()
        output = av.open(
            sink,
            "w",
            format="mp4",
            options={"movflags": _MOVFLAGS, "flush_packets": "1"},
        )
        try:
            add_stream_copy(output, template)
        except BaseException:
            output.close()
            raise
        return cls(output, sink)

    def push(self, packet: EncodedPacket) -> list[Fragment]:
        """Mux packet; return the fragments completed by it, in order."""
        before = self._remuxer.count
        self._remuxer.mux(packet)
        if self._remuxer.count > before:
            self._pending.append(packet.is_keyframe)
        return self._take_fragments()

    def close(self) -> None:
        with av_io_lock:
            self._output.close()

    def _take_fragments(self) -> list[Fragment]:
        fragments: list[Fragment] = []
        for box_type, box in _take_boxes(self._sink.buffer):
            if self.init is None:
                self._parts.append((box_type, box))
                if box_type == "moov":
                    data = b"".join(part for _, part in self._parts)
                    self._parts.clear()
                    codec = codec_string(data)
                    if codec is None:
                        raise ValueError("Unsupported codec for live viewing")
                    self.init = InitSegment(f'video/mp4; codecs="{codec}"', data)
            elif box_type == "moof":
                self._parts = [(box_type, box)]
            elif box_type == "mdat" and self._parts and self._pending:
                data = self._parts[0][1] + box
                self._parts.clear()
                fragments.append(Fragment(data, self._pending.popleft()))
        return fragments


def _take_boxes(buffer: bytearray) -> list[tuple[str, bytes]]:
    """Remove and return the complete top-level boxes at buffer's start."""
    boxes: list[tuple[str, bytes]] = []
    offset = 0
    while len(buffer) - offset >= _BOX_HEADER.size:
        size, box_type = _BOX_HEADER.unpack_from(buffer, offset)
        if size < _BOX_HEADER.size or len(buffer) - offset < size:
            break
        boxes.append(
            (box_type.decode("latin-1"), bytes(buffer[offset : offset + size]))
        )
        offset += size
    del buffer[:offset]
    return boxes


def codec_string(init: bytes) -> str | None:
    """The RFC 6381 codecs value for an init segment's video, e.g.
    "avc1.64001f" or "hvc1.1.6.L93.B0", or None for any other codec."""
    avc = init.find(b"avcC")
    if avc != -1 and len(init) >= avc + 8:
        profile, compatibility, level = init[avc + 5 : avc + 8]
        return f"avc1.{profile:02x}{compatibility:02x}{level:02x}"
    hvc = init.find(b"hvcC")
    if hvc != -1 and len(init) >= hvc + 17:
        return _hevc_codec_string(init[hvc + 4 : hvc + 17])
    return None


def _hevc_codec_string(config: bytes) -> str:
    """ISO/IEC 14496-15 annex E string from an hvcC record's first 13 bytes."""
    profile_space = config[1] >> 6
    tier = "H" if config[1] & 0x20 else "L"
    profile_idc = config[1] & 0x1F
    compatibility = int.from_bytes(config[2:6], "big")
    # The compatibility flags are written in reverse bit order.
    reversed_flags = int(f"{compatibility:032b}"[::-1], 2)
    constraints = config[6:12].rstrip(b"\x00")
    level = config[12]
    space = "" if profile_space == 0 else "ABC"[profile_space - 1]
    parts = [f"hvc1.{space}{profile_idc}", f"{reversed_flags:X}", f"{tier}{level}"]
    parts += [f"{byte:X}" for byte in constraints]
    return ".".join(parts)
