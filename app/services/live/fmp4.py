"""Fragmented MP4 for live viewing.

The camera's packets are remuxed (never re-encoded) into an init segment
(ftyp + moov) followed by one fragment (moof + mdat) per video frame or
audio packet, the format browsers append to a <video> through Media Source
Extensions.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass

import av
from av.audio.stream import AudioStream
from av.container import OutputContainer
from av.video.stream import VideoStream

from app.services.av_io import av_io_lock
from app.services.recording.packets import EncodedPacket
from app.services.remux import Remuxer, add_stream_copy

# empty_moov: the init segment holds no samples, so it can be sent on its
# own. default_base_moof: each fragment is self-contained, so a viewer can
# start at any keyframe. frag_keyframe: a fragment starts at every keyframe.
_MOVFLAGS = "frag_keyframe+empty_moov+default_base_moof"
# A fragment as soon as it holds any duration at all: one per video frame
# (plus the audio in between), so the viewer is never a whole GOP behind.
# Unlike frag_every_frame, this never closes a fragment before its samples'
# durations are known, which with audio left zero-length samples: gaps that
# stall a browser's playback.
_MUXER_OPTIONS = {"movflags": _MOVFLAGS, "frag_duration": "1", "flush_packets": "1"}
_BOX_HEADER = struct.Struct(">I4s")
_UINT32 = struct.Struct(">I")
# FFmpeg numbers tracks in the order streams were added: video first.
_VIDEO_TRACK_ID = 1
# ISO/IEC 14496-12 sample flags: set for samples a decoder can't start at.
_SAMPLE_IS_NON_SYNC = 0x10000


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
    """Turns a camera's packets into an init segment and fragments.

    FFmpeg writes a fragment only once a later packet arrives (it needs that
    timestamp for the sample's duration) and interleaves audio with video,
    so fragments don't map one-to-one to the packets pushed: whether a
    fragment starts with a video keyframe is read from the fragment itself.
    """

    def __init__(
        self, output: OutputContainer, sink: _Sink, audio_codec: str | None = None
    ) -> None:
        self._output = output
        self._sink = sink
        self._audio_codec = audio_codec
        self._remuxer = Remuxer(output)
        self._parts: list[tuple[str, bytes]] = []
        self.init: InitSegment | None = None

    @classmethod
    def open(
        cls, video: VideoStream, audio: AudioStream | None = None
    ) -> Fmp4Segmenter:
        """Copy video's codec, and audio's if given (see audio_codec_string).

        Must be called while the streams' input container is open, with
        av_io_lock held (see RTSPCamera._open_live_output).
        """
        audio_codec = audio_codec_string(audio) if audio is not None else None
        if audio is not None and audio_codec is None:
            raise ValueError("Unsupported audio codec for live viewing")
        sink = _Sink()
        output = av.open(
            sink,
            "w",
            format="mp4",
            options=_MUXER_OPTIONS,
        )
        try:
            add_stream_copy(output, video)
            if audio is not None:
                output.add_stream_from_template(audio)
        except BaseException:
            output.close()
            raise
        return cls(output, sink, audio_codec)

    def push(self, packet: EncodedPacket) -> list[Fragment]:
        """Mux packet; return the fragments completed by it, in order."""
        self._remuxer.mux(packet)
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
                    if self._audio_codec is not None:
                        codec += f",{self._audio_codec}"
                    self.init = InitSegment(f'video/mp4; codecs="{codec}"', data)
            elif box_type == "moof":
                self._parts = [(box_type, box)]
            elif box_type == "mdat" and self._parts:
                moof = self._parts[0][1]
                self._parts.clear()
                fragments.append(
                    Fragment(moof + box, _starts_with_video_keyframe(moof))
                )
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


def _children(data: bytes, start: int, end: int) -> list[tuple[str, int, int]]:
    """(type, payload start, end) of the boxes in data[start:end]."""
    boxes: list[tuple[str, int, int]] = []
    while end - start >= _BOX_HEADER.size:
        size, box_type = _BOX_HEADER.unpack_from(data, start)
        if size < _BOX_HEADER.size or start + size > end:
            break
        boxes.append(
            (box_type.decode("latin-1"), start + _BOX_HEADER.size, start + size)
        )
        start += size
    return boxes


def _starts_with_video_keyframe(moof: bytes) -> bool:
    """True if moof's video track fragment starts with a sync sample."""
    for box_type, start, end in _children(moof, _BOX_HEADER.size, len(moof)):
        if box_type != "traf":
            continue
        track_id: int | None = None
        default_flags: int | None = None
        first_flags: int | None = None
        for child, payload, child_end in _children(moof, start, end):
            if child_end - payload < 8:
                continue
            flags = _UINT32.unpack_from(moof, payload)[0] & 0xFFFFFF
            if child == "tfhd":
                track_id = _UINT32.unpack_from(moof, payload + 4)[0]
                # Skip the optional fields before default-sample-flags.
                offset = payload + 8
                offset += 8 if flags & 0x01 else 0  # base-data-offset
                offset += 4 if flags & 0x02 else 0  # sample-description-index
                offset += 4 if flags & 0x08 else 0  # default-sample-duration
                offset += 4 if flags & 0x10 else 0  # default-sample-size
                if flags & 0x20 and offset + 4 <= child_end:
                    default_flags = _UINT32.unpack_from(moof, offset)[0]
            elif child == "trun":
                offset = payload + 8 + (4 if flags & 0x01 else 0)  # data-offset
                if flags & 0x04 and offset + 4 <= child_end:  # first-sample-flags
                    first_flags = _UINT32.unpack_from(moof, offset)[0]
                elif flags & 0x400:  # per-sample flags
                    offset += 4 if flags & 0x100 else 0  # sample-duration
                    offset += 4 if flags & 0x200 else 0  # sample-size
                    if offset + 4 <= child_end:
                        first_flags = _UINT32.unpack_from(moof, offset)[0]
        sample_flags = first_flags if first_flags is not None else default_flags
        if track_id == _VIDEO_TRACK_ID and sample_flags is not None:
            return not sample_flags & _SAMPLE_IS_NON_SYNC
    return False


def audio_codec_string(stream: AudioStream) -> str | None:
    """The RFC 6381 codecs value for an AAC stream, e.g. "mp4a.40.2", or
    None for any other audio codec (only AAC is passed through)."""
    config = stream.codec_context.extradata or b""
    if stream.codec_context.name != "aac" or not config:
        return None
    object_type = config[0] >> 3
    if object_type == 31 and len(config) >= 2:  # escape: 32 + 6 more bits
        object_type = 32 + (((config[0] & 0x07) << 3) | (config[1] >> 5))
    return f"mp4a.40.{object_type}"


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
