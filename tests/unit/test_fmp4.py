"""Tests for Fmp4Segmenter: real H.264 and HEVC remuxed into playable fMP4."""

from __future__ import annotations

import dataclasses
import io
from unittest.mock import MagicMock, patch

import av
import pytest

from app.services.av_io import av_io_lock
from app.services.live.fmp4 import Fmp4Segmenter, Fragment, _Sink, codec_string
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
