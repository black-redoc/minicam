"""A fake fMP4 segmenter and packets for live-feed tests."""

from __future__ import annotations

from fractions import Fraction
from typing import cast

from app.services.live.feed import LiveFeed
from app.services.live.fmp4 import Fmp4Segmenter, Fragment, InitSegment
from app.services.recording.packets import EncodedPacket

INIT = InitSegment('video/mp4; codecs="avc1.64001f"', b"init")


def packet(n: int, keyframe: bool = False) -> EncodedPacket:
    return EncodedPacket(
        data=bytes([n]),
        pts=n,
        dts=n,
        time_base=Fraction(1, 10),
        is_keyframe=keyframe,
        received_at=float(n),
    )


class FakeSegmenter:
    """Emits the init segment on the first push and one fragment per packet
    (without FFmpeg's one-frame delay, which test_fmp4 covers)."""

    def __init__(self) -> None:
        self.init: InitSegment | None = None
        self.closed = False
        self.fail_push = False
        self.fail_close = False

    def push(self, packet: EncodedPacket) -> list[Fragment]:
        if self.fail_push:
            raise RuntimeError("mux failed")
        self.init = INIT
        return [Fragment(packet.data, packet.is_keyframe)]

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


def open_feed(feed: LiveFeed) -> FakeSegmenter:
    segmenter = FakeSegmenter()
    feed.on_stream_opened(lambda: cast(Fmp4Segmenter, segmenter))
    return segmenter
