"""Real H.264 and HEVC packets for recording tests, encoded with PyAV's
bundled libx264 and libx265.

The stream is muxed into in-memory MPEG-TS so its packets are Annex B with
the parameter sets in the codec extradata, the same shape the RTSP demuxer
produces.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from typing import Literal

import av
import numpy as np
from av.container import InputContainer
from av.video.stream import VideoStream

from app.services.recording.packets import EncodedPacket

WIDTH = 64
HEIGHT = 48
FPS = 10

Codec = Literal["h264", "hevc"]

# No B-frames and no scene-cut keyframes, so keyframes land exactly every
# `gop` frames whatever the content.
_ENCODERS: dict[Codec, tuple[str, dict[str, str]]] = {
    "h264": ("libx264", {"bf": "0", "sc_threshold": "0"}),
    "hevc": ("libx265", {"x265-params": "log-level=none:bframes=0:scenecut=0"}),
}


def encode_ts(frames: int = 30, gop: int = 5, codec: Codec = "h264") -> bytes:
    """Encode `frames` frames at FPS with a keyframe every `gop` frames."""
    encoder, options = _ENCODERS[codec]
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mpegts") as output:
        stream = output.add_stream(encoder, rate=FPS)
        assert isinstance(stream, VideoStream)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.codec_context.gop_size = gop
        stream.codec_context.options = options
        for i in range(frames):
            image = np.full((HEIGHT, WIDTH, 3), (i * 8) % 256, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = i
            output.mux(stream.encode(frame))
        output.mux(stream.encode())
    return buffer.getvalue()


@contextmanager
def open_source(
    frames: int = 30, gop: int = 5, codec: Codec = "h264"
) -> Iterator[tuple[InputContainer, VideoStream]]:
    """An open input container (and its video stream) over encoded video."""
    container = av.open(io.BytesIO(encode_ts(frames, gop, codec)), "r")
    try:
        yield container, container.streams.video[0]
    finally:
        container.close()


def video_packets(
    frames: int = 30, gop: int = 5, codec: Codec = "h264"
) -> list[EncodedPacket]:
    """EncodedPackets with received_at spaced 1/FPS apart from t=0."""
    packets: list[EncodedPacket] = []
    with open_source(frames, gop, codec) as (container, stream):
        time_base = Fraction(stream.time_base or Fraction(1, 90000))
        for packet in container.demux(stream):
            encoded = EncodedPacket.from_av(packet, time_base, len(packets) / FPS)
            if encoded is not None:
                packets.append(encoded)
    return packets
