"""Tests for Fmp4Segmenter: real H.264 and HEVC remuxed into playable fMP4."""

from __future__ import annotations

import dataclasses
import io
from fractions import Fraction
from unittest.mock import MagicMock, patch

import av
import pytest

from app.services.av_io import av_io_lock
from app.services.live.fmp4 import (
    Fmp4Segmenter,
    Fragment,
    _Sink,
    _starts_with_video_keyframe,
    audio_codec_string,
    codec_string,
)
from app.services.recording.packets import EncodedPacket, PacketKind
from tests.fixtures.video import Codec, open_source, video_packets


def _segment(codec: Codec, frames: int = 12) -> tuple[Fmp4Segmenter, list[Fragment]]:
    packets = video_packets(frames=frames, gop=5, codec=codec)
    with open_source(codec=codec) as (_container, stream), av_io_lock:
        segmenter = Fmp4Segmenter.open(stream)
    fragments: list[Fragment] = []
    for packet in packets:
        fragments += segmenter.push(packet)
    segmenter.close()
    return segmenter, fragments


def _box_types(data: bytes) -> list[str]:
    types: list[str] = []
    offset = 0
    while offset < len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        types.append(data[offset + 4 : offset + 8].decode())
        offset += size
    return types


@pytest.mark.parametrize(
    ("codec", "prefix"),
    [("h264", 'video/mp4; codecs="avc1.'), ("hevc", 'video/mp4; codecs="hvc1.')],
)
def test_init_segment_and_one_fragment_per_frame(codec: Codec, prefix: str) -> None:
    segmenter, fragments = _segment(codec)

    assert segmenter.init is not None
    assert segmenter.init.mime.startswith(prefix)
    assert _box_types(segmenter.init.data) == ["ftyp", "moov"]
    # A frame's fragment is written once the next packet arrives.
    assert len(fragments) == 11
    assert [f.is_keyframe for f in fragments] == [i % 5 == 0 for i in range(11)]
    assert all(_box_types(f.data) == ["moof", "mdat"] for f in fragments)


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_init_plus_fragments_decode(codec: Codec) -> None:
    segmenter, fragments = _segment(codec)
    assert segmenter.init is not None

    data = segmenter.init.data + b"".join(f.data for f in fragments)
    with av.open(io.BytesIO(data), "r") as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == codec
        assert sum(1 for _ in container.decode(stream)) == len(fragments)


def test_packets_without_timestamps_produce_no_fragment() -> None:
    packets = video_packets(frames=6, gop=5)
    with open_source() as (_container, stream), av_io_lock:
        segmenter = Fmp4Segmenter.open(stream)
    fragments = segmenter.push(packets[0])
    fragments += segmenter.push(dataclasses.replace(packets[1], pts=None, dts=None))
    fragments += segmenter.push(packets[2])
    segmenter.close()

    assert [f.is_keyframe for f in fragments] == [True]


def test_open_closes_the_output_if_the_stream_cannot_be_added() -> None:
    output = MagicMock()
    output.add_stream_from_template.side_effect = ValueError("no codec")
    with (
        patch("app.services.live.fmp4.av.open", return_value=output),
        pytest.raises(ValueError),
    ):
        Fmp4Segmenter.open(MagicMock())
    output.close.assert_called_once()


def test_unsupported_codec_is_refused() -> None:
    output = MagicMock()
    output.streams.video = [MagicMock()]
    sink = _Sink()
    segmenter = Fmp4Segmenter(output, sink)
    sink.write(b"\x00\x00\x00\x08ftyp\x00\x00\x00\x08moov")
    with pytest.raises(ValueError, match="Unsupported codec"):
        segmenter.push(
            dataclasses.replace(video_packets(frames=1)[0], pts=None, dts=None)
        )


def _hvcc(
    profile_byte: int, compatibility: bytes, constraint: int, level: int
) -> bytes:
    config = bytes([1, profile_byte]) + compatibility
    config += bytes([constraint, 0, 0, 0, 0, 0, level])
    return b"\x00\x00\x00\x2fhvcC" + config


@pytest.mark.parametrize(
    ("init", "expected"),
    [
        (b"....avcC\x01\x64\x00\x1f", "avc1.64001f"),
        (b"....avcC\x01\x42\xc0\x1e", "avc1.42c01e"),
        (_hvcc(0x01, b"\x60\x00\x00\x00", 0xB0, 93), "hvc1.1.6.L93.B0"),
        (_hvcc(0x22, b"\x20\x00\x00\x00", 0xB0, 120), "hvc1.2.4.H120.B0"),
        (_hvcc(0x41, b"\x40\x00\x00\x00", 0x00, 90), "hvc1.A1.2.L90"),
        (b"ftyp no codec configuration", None),
        (b"....avcC\x01", None),
    ],
)
def test_codec_string(init: bytes, expected: str | None) -> None:
    assert codec_string(init) == expected


def _av_packets(frames: int = 12) -> list[EncodedPacket]:
    """Video and audio packets of the A/V fixture, in demux order."""
    packets: list[EncodedPacket] = []
    with open_source(frames=frames, gop=5, audio=True) as (container, _video):
        for packet in container.demux():
            kind: PacketKind = "audio" if packet.stream.type == "audio" else "video"
            time_base = Fraction(packet.stream.time_base or Fraction(1, 1000))
            encoded = EncodedPacket.from_av(packet, time_base, 0.0, kind=kind)
            if encoded is not None:
                packets.append(encoded)
    return packets


def test_video_with_audio_segments_and_decodes() -> None:
    with open_source(frames=12, gop=5, audio=True) as (container, video), av_io_lock:
        segmenter = Fmp4Segmenter.open(video, container.streams.audio[0])
    fragments: list[Fragment] = []
    for packet in _av_packets():
        fragments += segmenter.push(packet)
    segmenter.close()

    assert segmenter.init is not None
    assert segmenter.init.mime.endswith(',mp4a.40.2"')
    # Keyframes are video keyframes only, though every AAC packet is one.
    assert sum(f.is_keyframe for f in fragments) == 3
    assert fragments[0].is_keyframe
    data = segmenter.init.data + b"".join(f.data for f in fragments)
    with av.open(io.BytesIO(data), "r") as output:
        assert {s.type for s in output.streams} == {"video", "audio"}
        audio = sum(1 for p in output.demux(output.streams.audio[0]) if p.size)
        assert audio >= 15


def test_audio_without_an_audio_track_is_ignored() -> None:
    with open_source(frames=12, gop=5, audio=True) as (_container, video), av_io_lock:
        segmenter = Fmp4Segmenter.open(video)
    fragments: list[Fragment] = []
    for packet in _av_packets():
        fragments += segmenter.push(packet)
    segmenter.close()

    assert segmenter.init is not None
    assert "mp4a" not in segmenter.init.mime
    assert len(fragments) == 11


def test_unsupported_audio_is_refused() -> None:
    audio = MagicMock()
    audio.codec_context.name = "pcm_alaw"
    with pytest.raises(ValueError, match="Unsupported audio codec"):
        Fmp4Segmenter.open(MagicMock(), audio)


@pytest.mark.parametrize(
    ("name", "extradata", "expected"),
    [
        ("aac", b"\x14\x08", "mp4a.40.2"),
        ("aac", b"\x0a\x10", "mp4a.40.1"),
        ("aac", b"\xf8\xe0", "mp4a.40.39"),  # escaped object type 32 + 7
        ("aac", b"", None),
        ("aac", None, None),
        ("pcm_mulaw", b"\x14\x08", None),
    ],
)
def test_audio_codec_string(
    name: str, extradata: bytes | None, expected: str | None
) -> None:
    stream = MagicMock()
    stream.codec_context.name = name
    stream.codec_context.extradata = extradata
    assert audio_codec_string(stream) == expected


def _box(box_type: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _full_box(box_type: bytes, flags: int, payload: bytes) -> bytes:
    return _box(box_type, flags.to_bytes(4, "big") + payload)


def _moof(
    track_id: int,
    tfhd_flags: int,
    tfhd_fields: bytes,
    trun_flags: int,
    trun_fields: bytes,
) -> bytes:
    tfhd = _full_box(b"tfhd", tfhd_flags, track_id.to_bytes(4, "big") + tfhd_fields)
    trun = _full_box(b"trun", trun_flags, (1).to_bytes(4, "big") + trun_fields)
    return _box(b"moof", _box(b"mfhd", bytes(8)) + _box(b"traf", tfhd + trun))


_SYNC = (0x02000000).to_bytes(4, "big")
_NON_SYNC = (0x01010000).to_bytes(4, "big")


@pytest.mark.parametrize(
    ("moof", "expected"),
    [
        # first-sample-flags in trun
        (_moof(1, 0, b"", 0x05, bytes(4) + _SYNC), True),
        (_moof(1, 0, b"", 0x05, bytes(4) + _NON_SYNC), False),
        # default-sample-flags in tfhd, after every optional field
        (_moof(1, 0x3B, bytes(8 + 4 + 4 + 4) + _SYNC, 0x01, bytes(4)), True),
        (_moof(1, 0x20, _NON_SYNC, 0x01, bytes(4)), False),
        # per-sample flags in trun, after duration and size
        (_moof(1, 0, b"", 0x701, bytes(4) + bytes(8) + _SYNC), True),
        # the audio track, or no sample flags at all
        (_moof(2, 0, b"", 0x05, bytes(4) + _SYNC), False),
        (_moof(1, 0, b"", 0x01, bytes(4)), False),
        # truncated boxes
        (_box(b"moof", _box(b"traf", _box(b"tfhd", b"\x00"))), False),
        (_box(b"moof", b"\x00\x00\x00\x40traf"), False),
    ],
)
def test_keyframe_is_read_from_the_fragment(moof: bytes, expected: bool) -> None:
    assert _starts_with_video_keyframe(moof) is expected
