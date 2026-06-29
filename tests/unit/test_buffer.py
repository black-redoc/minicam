import threading
import time

import pytest

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.models.frame import Frame
from tests.fixtures.camera import make_frame


class TestDoubleFrameBufferBasics:
    def test_initial_read_returns_none(self) -> None:
        buf = DoubleFrameBuffer()
        assert buf.read() is None

    def test_write_then_read_returns_frame(self) -> None:
        buf = DoubleFrameBuffer()
        frame = make_frame(0)
        buf.write(frame)
        time.sleep(0.01)  # allow executor to process
        assert buf.read() is frame

    def test_second_write_replaces_first(self) -> None:
        buf = DoubleFrameBuffer()
        buf.write(make_frame(0))
        f1 = make_frame(1)
        buf.write(f1)
        time.sleep(0.01)
        assert buf.read() is f1

    def test_multiple_reads_return_same_object(self) -> None:
        buf = DoubleFrameBuffer()
        frame = make_frame(0)
        buf.write(frame)
        time.sleep(0.01)
        assert buf.read() is buf.read()

    def test_latest_sequence_is_visible(self) -> None:
        buf = DoubleFrameBuffer()
        for i in range(10):
            buf.write(make_frame(i))
        time.sleep(0.05)
        result = buf.read()
        assert result is not None
        assert result.sequence == 9

    def test_shutdown_does_not_raise(self) -> None:
        buf = DoubleFrameBuffer()
        buf.write(make_frame(0))
        buf.shutdown()


class TestDoubleFrameBufferConcurrency:
    def test_concurrent_writes_do_not_raise(self) -> None:
        buf = DoubleFrameBuffer()
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

        time.sleep(0.05)
        assert not errors

    def test_concurrent_readers_get_valid_frames_or_none(self) -> None:
        buf = DoubleFrameBuffer()
        buf.write(make_frame(0))
        time.sleep(0.01)
        seen: list[Frame | None] = []

        def reader() -> None:
            for _ in range(50):
                seen.append(buf.read())

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(f is None or isinstance(f, Frame) for f in seen)

    def test_write_read_interleaved_does_not_corrupt_type(self) -> None:
        buf = DoubleFrameBuffer()
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
        time.sleep(0.05)
        stop.set()
        for r in readers:
            r.join()

        assert not errors
