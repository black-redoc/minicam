"""Real H.264 and HEVC packets (optionally with AAC audio) for recording
and live tests, encoded with PyAV's bundled libx264, libx265 and aac.

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
from av.audio.stream import AudioStream
from av.container import InputContainer
from av.video.stream import VideoStream

from app.services.recording.packets import EncodedPacket

WIDTH = 64
HEIGHT = 48
FPS = 10
SAMPLE_RATE = 16000
_AUDIO_FRAME_SAMPLES = 1024

Codec = Literal["h264", "hevc"]

# No B-frames and no scene-cut keyframes, so keyframes land exactly every
# `gop` frames whatever the content.
_ENCODERS: dict[Codec, tuple[str, dict[str, str]]] = {
    "h264": ("libx264", {"bf": "0", "sc_threshold": "0"}),
    "hevc": ("libx265", {"x265-params": "log-level=none:bframes=0:scenecut=0"}),
}


def encode_ts(
    frames: int = 30, gop: int = 5, codec: Codec = "h264", audio: bool = False
) -> bytes:
    """Encode `frames` frames at FPS with a keyframe every `gop` frames, and
    with audio, an AAC mono tone at SAMPLE_RATE spanning the same time.

    With audio the container is Matroska rather than MPEG-TS: in MPEG-TS
    AAC is ADTS-framed with no codec extradata, while an RTSP source (and
    Matroska) provides the AudioSpecificConfig.
    """
    encoder, options = _ENCODERS[codec]
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="matroska" if audio else "mpegts") as output:
        stream = output.add_stream(encoder, rate=FPS)
        assert isinstance(stream, VideoStream)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.codec_context.gop_size = gop
        stream.codec_context.options = options
        audio_stream = output.add_stream("aac", rate=SAMPLE_RATE) if audio else None
        if audio_stream is not None:
            assert isinstance(audio_stream, AudioStream)
            audio_stream.layout = "mono"
        samples = 0
        for i in range(frames):
            image = np.full((HEIGHT, WIDTH, 3), (i * 8) % 256, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = i
            output.mux(stream.encode(frame))
            # Audio up to the end of this video frame, in AAC-sized frames.
            while audio_stream is not None and samples < (i + 1) * SAMPLE_RATE // FPS:
                t = (samples + np.arange(_AUDIO_FRAME_SAMPLES)) / SAMPLE_RATE
                tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
                audio_frame = av.AudioFrame.from_ndarray(
                    tone.reshape(1, -1), format="fltp", layout="mono"
                )
                audio_frame.sample_rate = SAMPLE_RATE
                audio_frame.pts = samples
                output.mux(audio_stream.encode(audio_frame))
                samples += _AUDIO_FRAME_SAMPLES
        output.mux(stream.encode())
        if audio_stream is not None:
            output.mux(audio_stream.encode())
    return buffer.getvalue()


@contextmanager
def open_source(
    frames: int = 30, gop: int = 5, codec: Codec = "h264", audio: bool = False
) -> Iterator[tuple[InputContainer, VideoStream]]:
    """An open input container (and its video stream) over encoded video."""
    container = av.open(io.BytesIO(encode_ts(frames, gop, codec, audio)), "r")
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
