"""RTSPCamera + CameraRecorder + ClipWriter end to end, on real H.264.

av.open is patched to return an in-memory MPEG-TS container instead of an
RTSP connection; everything downstream (demux, decode, packet copies, the
clip's template stream and the MP4 remux) is the real code path.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import av

from app.buffers.frame_buffer import FrameBuffer
from app.services.camera import RTSPCamera
from app.services.recording.packets import ClipStart
from app.services.recording.recorder import CameraRecorder
from app.services.recording.writer import ClipWriter, FinishedClip, _Muxer
from tests.fixtures.h264 import encode_h264_ts


def test_records_a_playable_clip_from_the_live_stream(tmp_path: Path) -> None:
    writers: list[ClipWriter] = []
    finished: list[FinishedClip] = []

    def open_clip(start: ClipStart) -> ClipWriter | None:
        writer = ClipWriter.open(
            start, tmp_path / f"clip{len(writers)}.mp4", finished.append
        )
        if writer is not None:
            writers.append(writer)
        return writer

    recorder = CameraRecorder("entrance", open_clip)
    recorder.arm(True)
    recorder.record_until(math.inf)
    buf = FrameBuffer()
    cam = RTSPCamera("rtsp://u:p@cam/stream", buf, name="entrance", recorder=recorder)
    cam._running = True
    container = av.open(io.BytesIO(encode_h264_ts(frames=20, gop=5)), "r")
    real_open = av.open

    def fake_open(url: object, *args: object, **kwargs: object) -> object:
        # av.open is one function for the whole process: only the RTSP
        # input is faked, the clip writer's MP4 output stays real.
        if isinstance(url, str) and url.startswith("rtsp://"):
            return container
        return real_open(url, *args, **kwargs)

    with patch("app.services.camera.av.open", side_effect=fake_open):
        cam._capture_stream()

    assert buf.read() is not None  # live view still gets decoded frames
    [writer] = writers  # the stream end closed the one clip
    assert writer.join(10)
    [clip] = finished
    with av.open(str(clip.path)) as output:
        stream = output.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert sum(1 for _ in output.decode(stream)) >= 19


def test_clip_output_is_refused_once_the_input_is_closed(tmp_path: Path) -> None:
    cam = RTSPCamera("rtsp://cam/stream", FrameBuffer())
    cam._running = True
    assert cam._open_clip_output(tmp_path / "clip.mp4") is None

    cam._container = MagicMock()
    cam._running = False
    assert cam._open_clip_output(tmp_path / "clip.mp4") is None


def test_muxer_without_packets_has_zero_duration() -> None:
    output = MagicMock()
    output.streams.video = [MagicMock()]
    assert _Muxer(output).duration_seconds == 0.0
