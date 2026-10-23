"""RTSPCamera + LiveFeed + Fmp4Segmenter end to end, on real H.264 and HEVC.

av.open is patched to return an in-memory MPEG-TS container instead of an
RTSP connection; demux, decode, the segmenter's template stream and the
fMP4 remux are the real code path.
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import MagicMock, patch

import av
import pytest

from app.buffers.frame_buffer import FrameBuffer
from app.services.av_clock import AudioClockAligner
from app.services.camera import RTSPCamera
from app.services.live.feed import LiveFeed, LiveMessage
from app.services.live.fmp4 import InitSegment
from tests.fixtures.video import Codec, encode_ts


@pytest.mark.parametrize("codec", ["h264", "hevc"])
async def test_viewer_receives_playable_fmp4_from_the_live_stream(codec: Codec) -> None:
    feed = LiveFeed()
    buf = FrameBuffer()
    cam = RTSPCamera("rtsp://u:p@cam/stream", buf, name="entrance", live_feed=feed)
    cam._running = True
    container = av.open(io.BytesIO(encode_ts(frames=20, gop=5, codec=codec)), "r")
    real_open = av.open

    def fake_open(url: object, *args: object, **kwargs: object) -> object:
        if isinstance(url, str) and url.startswith("rtsp://"):
            return container
        return real_open(url, *args, **kwargs)  # type: ignore[call-overload]

    messages: list[LiveMessage] = []
    with feed.subscribe() as subscription:
        with patch("app.services.camera.av.open", side_effect=fake_open):
            cam._capture_stream()
        await asyncio.sleep(0)
        while subscription._queue:
            messages.append(await subscription.next())

    assert buf.read() is not None  # MJPEG viewers still get decoded frames
    assert feed.mime is None  # the stream ended
    init, *fragments = messages
    assert isinstance(init, InitSegment)
    data = init.data + b"".join(m.data for m in fragments)
    with av.open(io.BytesIO(data), "r") as output:
        stream = output.streams.video[0]
        assert stream.codec_context.name == codec
        assert sum(1 for _ in output.decode(stream)) == len(fragments) >= 18


def test_live_output_is_refused_once_the_input_is_closed() -> None:
    cam = RTSPCamera("rtsp://cam/stream", FrameBuffer())
    cam._running = True
    assert cam._open_live_output() is None

    cam._container = MagicMock()
    cam._running = False
    assert cam._open_live_output() is None


async def test_viewer_with_audio_receives_playable_audio_and_video() -> None:
    feed = LiveFeed()
    cam = RTSPCamera("rtsp://u:p@cam/stream", FrameBuffer(), live_feed=feed)
    cam._running = True
    container = av.open(io.BytesIO(encode_ts(frames=20, gop=5, audio=True)), "r")
    real_open = av.open

    def fake_open(url: object, *args: object, **kwargs: object) -> object:
        if isinstance(url, str) and url.startswith("rtsp://"):
            return container
        return real_open(url, *args, **kwargs)  # type: ignore[call-overload]

    messages: list[LiveMessage] = []
    with (
        feed.subscribe(audio=True) as subscription,
        feed.subscribe() as video_only,
        # The fixture is demuxed in an instant: nothing to calibrate over.
        patch("app.services.camera.AudioClockAligner", lambda: AudioClockAligner(0.0)),
        patch("app.services.camera.av.open", side_effect=fake_open),
    ):
        cam._capture_stream()
        await asyncio.sleep(0)
        while subscription._queue:
            messages.append(await subscription.next())
        video_init = await video_only.next()

    init, *fragments = messages
    assert isinstance(init, InitSegment)
    assert init.mime.endswith(',mp4a.40.2"')
    assert isinstance(video_init, InitSegment)
    assert "mp4a" not in video_init.mime
    data = init.data + b"".join(m.data for m in fragments)
    with av.open(io.BytesIO(data), "r") as output:
        audio = output.streams.audio[0]
        assert audio.codec_context.name == "aac"
        assert sum(1 for p in output.demux(audio) if p.size) >= 25


def test_audio_is_not_read_without_a_live_feed() -> None:
    cam = RTSPCamera("rtsp://u:p@cam/stream", FrameBuffer())
    cam._running = True
    container = av.open(io.BytesIO(encode_ts(frames=5, gop=5, audio=True)), "r")
    with (
        patch("app.services.camera.av.open", return_value=container),
        patch("app.services.camera.audio_codec_string") as audio_codec,
        patch("app.services.camera.EncodedPacket.from_av") as from_av,
    ):
        cam._capture_stream()
    audio_codec.assert_not_called()
    from_av.assert_not_called()  # nothing records or streams packets
