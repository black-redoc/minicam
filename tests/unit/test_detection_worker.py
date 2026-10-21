"""Tests for app.services.detection.worker."""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import Callable

import numpy as np
import pytest

from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot, DetectionWorker

_IMAGE = np.zeros((4, 4, 3), dtype=np.uint8)
_ALLOWED_CLASSES = frozenset({"person"})
_POLL_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 0.001


class _FakeFrame:
    """Minimal stand-in for app.models.frame.Frame — only what the worker reads."""

    def __init__(self, sequence: int) -> None:
        self.image = _IMAGE
        self.sequence = sequence


class _FakeBuffer:
    """Buffer double exposing only the .read() method the worker depends on."""

    def __init__(self) -> None:
        self._frame: _FakeFrame | None = None

    def read(self) -> _FakeFrame | None:
        return self._frame

    def set_frame(self, sequence: int) -> None:
        self._frame = _FakeFrame(sequence)


class _FakeBackend:
    """Backend double whose detect() behavior is fully controlled per test."""

    def __init__(self) -> None:
        self.calls = 0
        self.raise_error = False
        self.result: list[Detection] = []
        self.release: threading.Event | None = None

    @property
    def available_classes(self) -> tuple[str, ...]:
        return ("person",)

    def detect(
        self, image: np.ndarray, allowed_classes: frozenset[str]
    ) -> list[Detection]:
        self.calls += 1
        if self.release is not None:
            self.release.wait(timeout=_POLL_TIMEOUT_SECONDS * 2)
        if self.raise_error:
            raise RuntimeError("boom")
        return self.result


def _make_worker(
    buffer: _FakeBuffer,
    backend: _FakeBackend,
    on_result: Callable[[DetectionSnapshot], None],
    interval_seconds: float = 0.01,
) -> DetectionWorker:
    return DetectionWorker(
        camera_name="cam1",
        buffer=buffer,  # type: ignore[arg-type]
        backend=backend,  # type: ignore[arg-type]
        allowed_classes=_ALLOWED_CLASSES,
        interval_seconds=interval_seconds,
        on_result=on_result,
    )


async def _wait_until(condition: Callable[[], bool]) -> None:
    """Poll condition() from the event loop side until true or time out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _POLL_TIMEOUT_SECONDS
    while not condition():
        if loop.time() > deadline:
            raise TimeoutError("condition was not met in time")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _cancel_and_await(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class TestDetectionSnapshot:
    def test_stores_fields(self) -> None:
        detection = Detection(
            class_name="person", confidence=0.9, x1=0, y1=0, x2=1, y2=1
        )
        snapshot = DetectionSnapshot(detections=[detection], sequence=5)
        assert snapshot.detections == [detection]
        assert snapshot.sequence == 5

    def test_is_frozen(self) -> None:
        snapshot = DetectionSnapshot(detections=[], sequence=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.sequence = 2  # type: ignore[misc]

    def test_has_no_dict_due_to_slots(self) -> None:
        snapshot = DetectionSnapshot(detections=[], sequence=1)
        assert not hasattr(snapshot, "__dict__")


class TestDetectionWorkerRun:
    async def test_does_not_call_on_result_when_frame_never_changes(self) -> None:
        buffer = _FakeBuffer()
        buffer.set_frame(sequence=1)
        backend = _FakeBackend()
        calls: list[DetectionSnapshot] = []
        worker = _make_worker(buffer, backend, calls.append, interval_seconds=0.01)

        task = asyncio.create_task(worker.run())
        await _wait_until(lambda: len(calls) >= 1)
        # Give the loop several more ticks at the same unchanged sequence.
        await asyncio.sleep(0.05)
        await _cancel_and_await(task)

        # Only the very first tick sees a "new" frame (None -> 1); every
        # later tick reads the same sequence and must skip detection
        # entirely, so backend.detect() is never called again.
        assert backend.calls == 1
        assert len(calls) == 1
        assert calls[0].sequence == 1

    async def test_calls_on_result_with_correct_snapshot_when_frame_changes(
        self,
    ) -> None:
        buffer = _FakeBuffer()
        buffer.set_frame(sequence=1)
        backend = _FakeBackend()
        detection = Detection(
            class_name="person", confidence=0.9, x1=0, y1=0, x2=1, y2=1
        )
        backend.result = [detection]
        calls: list[DetectionSnapshot] = []
        worker = _make_worker(buffer, backend, calls.append, interval_seconds=0.01)

        task = asyncio.create_task(worker.run())
        await _wait_until(lambda: len(calls) >= 1)
        buffer.set_frame(sequence=2)
        await _wait_until(lambda: len(calls) >= 2)
        await _cancel_and_await(task)

        assert calls[0] == DetectionSnapshot(detections=[detection], sequence=1)
        assert calls[1] == DetectionSnapshot(detections=[detection], sequence=2)

    async def test_backend_exception_is_logged_and_loop_keeps_running(self) -> None:
        buffer = _FakeBuffer()
        buffer.set_frame(sequence=1)
        backend = _FakeBackend()
        backend.raise_error = True
        calls: list[DetectionSnapshot] = []
        worker = _make_worker(buffer, backend, calls.append, interval_seconds=0.01)

        task = asyncio.create_task(worker.run())
        await _wait_until(lambda: backend.calls >= 2)
        assert calls == []

        # A later good frame proves the failed attempt did not corrupt state
        # or kill the task.
        backend.raise_error = False
        buffer.set_frame(sequence=2)
        await _wait_until(lambda: len(calls) >= 1)
        await _cancel_and_await(task)

        assert calls == [DetectionSnapshot(detections=[], sequence=2)]

    async def test_cancellation_during_inference_does_not_call_on_result(
        self,
    ) -> None:
        buffer = _FakeBuffer()
        buffer.set_frame(sequence=1)
        backend = _FakeBackend()
        backend.release = threading.Event()
        calls: list[DetectionSnapshot] = []
        worker = _make_worker(buffer, backend, calls.append, interval_seconds=0.01)

        task = asyncio.create_task(worker.run())
        # Wait until detect() has actually started running in the executor
        # thread, so cancellation below lands mid-inference rather than
        # before it starts.
        await _wait_until(lambda: backend.calls >= 1)

        task.cancel()
        # Unblock the executor thread only after cancellation was requested:
        # this is what proves the pending result is discarded on delivery,
        # not merely never produced because the thread never finished.
        backend.release.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert calls == []
