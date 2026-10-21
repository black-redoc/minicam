"""Tests for SharedJpegEncoder: one encode per frame, shared by all viewers."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.models.frame import Frame
from app.services.shared_encoder import SharedJpegEncoder
from tests.fixtures.camera import make_frame

_WAIT_TIMEOUT_SECONDS = 1.0


class _CountingRender:
    def __init__(self) -> None:
        self.calls: list[Frame] = []
        self.release: threading.Event | None = None
        self.error: Exception | None = None

    def __call__(self, frame: Frame) -> bytes:
        self.calls.append(frame)
        if self.release is not None:
            self.release.wait(timeout=_WAIT_TIMEOUT_SECONDS * 2)
        if self.error is not None:
            raise self.error
        return f"jpeg-{frame.sequence}".encode()


class TestSharedJpegEncoder:
    async def test_renders_and_returns_bytes(self) -> None:
        render = _CountingRender()
        frame = make_frame(1)

        assert await SharedJpegEncoder().encode(frame, render) == b"jpeg-1"
        assert render.calls == [frame]

    async def test_same_frame_is_rendered_once_for_sequential_viewers(self) -> None:
        encoder = SharedJpegEncoder()
        render = _CountingRender()
        frame = make_frame(1)

        results = [await encoder.encode(frame, render) for _ in range(3)]

        assert results == [b"jpeg-1"] * 3
        assert len(render.calls) == 1

    async def test_concurrent_viewers_share_one_in_flight_render(self) -> None:
        encoder = SharedJpegEncoder()
        render = _CountingRender()
        render.release = threading.Event()
        frame = make_frame(1)

        viewers = [asyncio.create_task(encoder.encode(frame, render)) for _ in range(5)]
        await asyncio.sleep(0.01)
        render.release.set()
        results = await asyncio.wait_for(
            asyncio.gather(*viewers), _WAIT_TIMEOUT_SECONDS
        )

        assert results == [b"jpeg-1"] * 5
        assert len(render.calls) == 1

    async def test_new_frame_triggers_a_new_render(self) -> None:
        encoder = SharedJpegEncoder()
        render = _CountingRender()

        await encoder.encode(make_frame(1), render)
        assert await encoder.encode(make_frame(2), render) == b"jpeg-2"
        assert len(render.calls) == 2

    async def test_frames_are_compared_by_identity_not_sequence(self) -> None:
        # A re-created camera restarts at sequence 0; its first frame must
        # never be served the old camera's cached JPEG.
        encoder = SharedJpegEncoder()
        render = _CountingRender()

        await encoder.encode(make_frame(0), render)
        await encoder.encode(make_frame(0), render)

        assert len(render.calls) == 2

    async def test_cancelling_one_viewer_does_not_cancel_the_others(self) -> None:
        encoder = SharedJpegEncoder()
        render = _CountingRender()
        render.release = threading.Event()
        frame = make_frame(1)

        leaving = asyncio.create_task(encoder.encode(frame, render))
        staying = asyncio.create_task(encoder.encode(frame, render))
        await asyncio.sleep(0.01)
        leaving.cancel()
        render.release.set()

        with pytest.raises(asyncio.CancelledError):
            await leaving
        assert await asyncio.wait_for(staying, _WAIT_TIMEOUT_SECONDS) == b"jpeg-1"

    async def test_render_error_reaches_viewers_and_next_frame_retries(self) -> None:
        encoder = SharedJpegEncoder()
        render = _CountingRender()
        render.error = RuntimeError("encode failed")

        with pytest.raises(RuntimeError, match="encode failed"):
            await encoder.encode(make_frame(1), render)

        render.error = None
        assert await encoder.encode(make_frame(2), render) == b"jpeg-2"
