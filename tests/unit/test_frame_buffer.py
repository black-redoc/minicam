import asyncio
import threading
from unittest.mock import patch

import pytest

from app.buffers.frame_buffer import FrameBuffer
from app.models.frame import Frame
from tests.fixtures.camera import make_frame

_WAIT_TIMEOUT_SECONDS = 1.0


class TestFrameBufferBasics:
    def test_initial_read_returns_none(self) -> None:
        buf = FrameBuffer()
        assert buf.read() is None

    def test_write_is_visible_immediately(self) -> None:
        buf = FrameBuffer()
        frame = make_frame(0)
        buf.write(frame)
        assert buf.read() is frame

    def test_second_write_replaces_first(self) -> None:
        buf = FrameBuffer()
        buf.write(make_frame(0))
        f1 = make_frame(1)
        buf.write(f1)
        assert buf.read() is f1

    def test_multiple_reads_return_same_object(self) -> None:
        buf = FrameBuffer()
        buf.write(make_frame(0))
        assert buf.read() is buf.read()

    def test_seconds_since_write_is_none_before_any_write(self) -> None:
        assert FrameBuffer().seconds_since_write(123.0) is None

    def test_seconds_since_write_measures_from_last_write(self) -> None:
        buf = FrameBuffer()
        with patch("app.buffers.frame_buffer.time.monotonic", return_value=100.0):
            buf.write(make_frame(0))
        assert buf.seconds_since_write(102.5) == pytest.approx(2.5)
        assert buf.seconds_since_write(99.0) == 0.0

    def test_write_after_shutdown_does_not_raise(self) -> None:
        buf = FrameBuffer()
        buf.shutdown()
        buf.write(make_frame(0))


class TestFrameBufferNextFrame:
    async def test_returns_latest_frame_immediately_when_new(self) -> None:
        buf = FrameBuffer()
        frame = make_frame(0)
        buf.write(frame)

        assert await buf.next_frame() is frame

    async def test_waits_for_a_write_when_empty(self) -> None:
        buf = FrameBuffer()
        waiter = asyncio.create_task(buf.next_frame())
        await asyncio.sleep(0)
        assert not waiter.done()

        frame = make_frame(0)
        buf.write(frame)

        assert await asyncio.wait_for(waiter, _WAIT_TIMEOUT_SECONDS) is frame

    async def test_does_not_return_the_frame_already_seen(self) -> None:
        buf = FrameBuffer()
        seen = make_frame(0)
        buf.write(seen)

        waiter = asyncio.create_task(buf.next_frame(after=seen))
        await asyncio.sleep(0.01)
        assert not waiter.done()

        fresh = make_frame(1)
        buf.write(fresh)
        assert await asyncio.wait_for(waiter, _WAIT_TIMEOUT_SECONDS) is fresh

    async def test_wakes_every_waiter_on_one_write(self) -> None:
        buf = FrameBuffer()
        waiters = [asyncio.create_task(buf.next_frame()) for _ in range(5)]
        await asyncio.sleep(0)

        frame = make_frame(0)
        buf.write(frame)

        results = await asyncio.wait_for(
            asyncio.gather(*waiters), _WAIT_TIMEOUT_SECONDS
        )
        assert all(result is frame for result in results)

    async def test_wakes_on_write_from_another_thread(self) -> None:
        buf = FrameBuffer()
        waiter = asyncio.create_task(buf.next_frame())
        await asyncio.sleep(0)

        frame = make_frame(0)
        writer = threading.Thread(target=buf.write, args=(frame,))
        writer.start()
        writer.join()

        assert await asyncio.wait_for(waiter, _WAIT_TIMEOUT_SECONDS) is frame

    async def test_skips_frames_a_slow_reader_missed(self) -> None:
        buf = FrameBuffer()
        first = make_frame(0)
        buf.write(first)
        assert await buf.next_frame() is first

        for i in range(1, 5):
            buf.write(make_frame(i))

        latest = await buf.next_frame(after=first)
        assert latest.sequence == 4

    async def test_write_after_loop_closed_is_ignored(self) -> None:
        buf = FrameBuffer()
        buf.write(make_frame(0))
        await buf.next_frame()  # binds the buffer to this loop
        loop = asyncio.new_event_loop()
        loop.close()
        buf._loop = loop

        buf.write(make_frame(1))  # must not raise

    async def test_cancelled_waiter_does_not_break_later_waiters(self) -> None:
        buf = FrameBuffer()
        cancelled = asyncio.create_task(buf.next_frame())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        waiter = asyncio.create_task(buf.next_frame())
        await asyncio.sleep(0)
        frame = make_frame(0)
        buf.write(frame)
        assert await asyncio.wait_for(waiter, _WAIT_TIMEOUT_SECONDS) is frame


class TestFrameBufferConcurrency:
    def test_concurrent_writes_do_not_raise(self) -> None:
        buf = FrameBuffer()
        errors: list[Exception] = []

        def writer(start: int) -> None:
            try:
                for i in range(100):
                    buf.write(make_frame(start + i))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i * 100,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert isinstance(buf.read(), Frame)

    def test_write_read_interleaved_does_not_corrupt_type(self) -> None:
        buf = FrameBuffer()
        errors: list[str] = []
        stop = threading.Event()

        def writer() -> None:
            for i in range(200):
                buf.write(make_frame(i))

        def reader() -> None:
            while not stop.is_set():
                frame = buf.read()
                if frame is not None and not isinstance(frame, Frame):
                    errors.append(f"unexpected type: {type(frame)}")

        readers = [threading.Thread(target=reader) for _ in range(4)]
        w = threading.Thread(target=writer)

        for r in readers:
            r.start()
        w.start()
        w.join()
        stop.set()
        for r in readers:
            r.join()

        assert not errors
