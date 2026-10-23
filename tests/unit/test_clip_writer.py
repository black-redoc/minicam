"""Tests for ClipWriter: real H.264 and HEVC packets remuxed into a playable MP4."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import av
import pytest

from app.services.recording.packets import ClipStart, EncodedPacket
from app.services.recording.writer import (
    ClipWriter,
    FinishedClip,
    open_output,
)
from tests.fixtures.video import (
    FPS,
    HEIGHT,
    WIDTH,
    Codec,
    open_source,
    video_packets,
)

_STARTED_AT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _write_clip(
    tmp_path: Path, packets: list[EncodedPacket], codec: Codec = "h264"
) -> tuple[Path, list[FinishedClip]]:
    finished: list[FinishedClip] = []
    path = tmp_path / "clip.mp4"
    with open_source(codec=codec) as (_container, stream):
        start = ClipStart("entrance", _STARTED_AT, lambda p: open_output(p, stream))
        writer = ClipWriter.open(start, path, finished.append)
    assert writer is not None
    for packet in packets:
        writer.write(packet)
    writer.finish()
    assert writer.join(10)
    return path, finished


def _decoded_frames(path: Path) -> int:
    with av.open(str(path)) as container:
        return sum(1 for _ in container.decode(container.streams.video[0]))


def test_writes_a_playable_mp4_from_a_keyframe(tmp_path: Path) -> None:
    packets = video_packets(frames=30, gop=5)[5:]  # starts on keyframe 5
    path, finished = _write_clip(tmp_path, packets)

    assert path.is_file()
    assert not path.with_name("clip.mp4.part").exists()
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert (stream.width, stream.height) == (WIDTH, HEIGHT)
        first = next(p for p in container.demux(stream) if p.size)
        assert first.pts == 0
        assert first.is_keyframe
    assert _decoded_frames(path) >= len(packets) - 1

    [clip] = finished
    assert clip.camera_name == "entrance"
    assert clip.path == path
    assert clip.started_at == _STARTED_AT
    assert clip.size_bytes == path.stat().st_size
    assert clip.duration_seconds == pytest.approx((len(packets) - 1) / FPS)


@pytest.mark.parametrize(("codec", "tag"), [("h264", "avc1"), ("hevc", "hvc1")])
def test_clip_keeps_the_camera_codec_with_a_tag_every_browser_plays(
    tmp_path: Path, codec: Codec, tag: str
) -> None:
    packets = video_packets(frames=10, gop=5, codec=codec)
    path, finished = _write_clip(tmp_path, packets, codec)

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == codec
        assert stream.codec_context.codec_tag == tag
    assert _decoded_frames(path) >= len(packets) - 1
    assert len(finished) == 1


def test_repeated_or_missing_timestamps_do_not_abort_the_clip(
    tmp_path: Path,
) -> None:
    packets = video_packets(frames=10, gop=5)
    broken = [
        packets[0],
        dataclasses.replace(packets[1], dts=packets[0].dts, pts=packets[0].pts),
        dataclasses.replace(packets[2], dts=None),
        *packets[3:],
    ]
    path, finished = _write_clip(tmp_path, broken)
    assert path.is_file()
    assert len(finished) == 1


def test_packets_without_any_timestamp_are_skipped(tmp_path: Path) -> None:
    packets = video_packets(frames=10, gop=5)
    unplaceable = dataclasses.replace(packets[1], pts=None, dts=None)
    path, finished = _write_clip(tmp_path, [packets[0], unplaceable, *packets[2:]])
    assert path.is_file()
    assert len(finished) == 1


def test_empty_clip_leaves_no_file(tmp_path: Path) -> None:
    path, finished = _write_clip(tmp_path, [])
    assert finished == []
    assert list(tmp_path.iterdir()) == []


def test_returns_none_when_the_input_is_gone(tmp_path: Path) -> None:
    start = ClipStart("entrance", _STARTED_AT, lambda p: None)
    assert ClipWriter.open(start, tmp_path / "clip.mp4", MagicMock()) is None


@pytest.fixture
def fake_av_packet() -> Iterator[None]:
    """A fake output has no real stream for av.Packet.stream to accept."""
    with patch("app.services.recording.writer.av.Packet"):
        yield


def _fake_output() -> MagicMock:
    output = MagicMock()
    output.streams.video = [MagicMock()]
    return output


def _started_writer(
    tmp_path: Path, output: MagicMock, max_queued: int = 2000
) -> tuple[ClipWriter, MagicMock]:
    on_finished = MagicMock()
    start = ClipStart("entrance", _STARTED_AT, lambda p: output)
    writer = ClipWriter(output, start, tmp_path / "clip.mp4", on_finished, max_queued)
    return writer, on_finished


@pytest.mark.usefixtures("fake_av_packet")
def test_mux_error_discards_the_clip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = _fake_output()
    output.mux.side_effect = ValueError("bad packet")
    writer, on_finished = _started_writer(tmp_path, output)
    writer.part_path.write_bytes(b"partial")
    for packet in video_packets(frames=5, gop=5):
        writer.write(packet)
    writer.finish()
    writer._run()

    on_finished.assert_not_called()
    assert not writer.part_path.exists()
    assert output.mux.call_count == 1  # gives up after the first failure
    assert "Recording for camera 'entrance' failed" in caplog.text


@pytest.mark.usefixtures("fake_av_packet")
def test_close_error_discards_the_clip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = _fake_output()
    output.close.side_effect = OSError("disk full")
    writer, on_finished = _started_writer(tmp_path, output)
    writer.part_path.write_bytes(b"partial")
    writer.write(video_packets(frames=1, gop=1)[0])
    writer.finish()
    writer._run()

    on_finished.assert_not_called()
    assert not writer.part_path.exists()
    assert "Could not finalize recording" in caplog.text


@pytest.mark.usefixtures("fake_av_packet")
def test_rename_error_discards_the_clip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = _fake_output()
    writer, on_finished = _started_writer(tmp_path, output)
    # The .part file was never created, so the rename fails.
    writer.write(video_packets(frames=1, gop=1)[0])
    writer.finish()
    writer._run()

    on_finished.assert_not_called()
    assert "Could not save recording" in caplog.text


def test_queue_overflow_abandons_the_clip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    output = _fake_output()
    writer, on_finished = _started_writer(tmp_path, output, max_queued=2)
    writer.part_path.write_bytes(b"partial")
    for packet in video_packets(frames=5, gop=5):
        writer.write(packet)
    writer.finish()
    writer._run()

    output.mux.assert_not_called()
    on_finished.assert_not_called()
    assert not writer.part_path.exists()
    assert caplog.text.count("fell behind") == 1


def test_open_output_closes_the_container_if_the_stream_cannot_be_added(
    tmp_path: Path,
) -> None:
    output = _fake_output()
    output.add_stream_from_template.side_effect = ValueError("no codec")
    with (
        patch("app.services.recording.writer.av.open", return_value=output),
        pytest.raises(ValueError),
    ):
        open_output(tmp_path / "clip.mp4", MagicMock())
    output.close.assert_called_once()
