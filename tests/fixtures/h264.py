"""Real H.264 packets for recording tests, encoded with PyAV's bundled libx264.

The stream is muxed into in-memory MPEG-TS so its packets are Annex B with
SPS/PPS in the codec extradata, the same shape the RTSP demuxer produces.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction

import av
import numpy as np
from av.container import InputContainer
from av.video.stream import VideoStream

from app.services.recording.packets import EncodedPacket

WIDTH = 64
HEIGHT = 48
FPS = 10


def encode_h264_ts(frames: int = 30, gop: int = 5) -> bytes:
    """Encode `frames` frames at FPS with a keyframe every `gop` frames."""
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mpegts") as output:
        stream = output.add_stream("libx264", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.codec_context.gop_size = gop
        stream.codec_context.options = {"bf": "0", "sc_threshold": "0"}
        for i in range(frames):
            image = np.full((HEIGHT, WIDTH, 3), (i * 8) % 256, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = i
            output.mux(stream.encode(frame))
        output.mux(stream.encode())
    return buffer.getvalue()


@contextmanager
def open_h264_source(
    frames: int = 30, gop: int = 5
) -> Iterator[tuple[InputContainer, VideoStream]]:
    """An open input container (and its video stream) over encoded H.264."""
    container = av.open(io.BytesIO(encode_h264_ts(frames, gop)), "r")
    try:
        yield container, container.streams.video[0]
    finally:
        container.close()


def h264_packets(frames: int = 30, gop: int = 5) -> list[EncodedPacket]:
    """EncodedPackets with received_at spaced 1/FPS apart from t=0."""
    packets: list[EncodedPacket] = []
    with open_h264_source(frames, gop) as (container, stream):
        time_base = Fraction(stream.time_base or Fraction(1, 90000))
        for packet in container.demux(stream):
            encoded = EncodedPacket.from_av(packet, time_base, len(packets) / FPS)
            if encoded is not None:
                packets.append(encoded)
    return packets
