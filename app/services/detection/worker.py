"""Per-camera background task that runs object detection at a fixed rate.

Decoupled from app.state on purpose: the worker only knows how to read
frames, run a backend, and report a result via a callback. Where that
result is stored (a module-level dict, a test double, anything) is the
caller's concern, which keeps this class testable without importing the
application's global state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.detection.base import Detection

if TYPE_CHECKING:
    from app.buffers.double_frame_buffer import DoubleFrameBuffer
    from app.services.detection.base import DetectionBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DetectionSnapshot:
    """Detections produced from a single source frame."""

    detections: list[Detection]
    sequence: int


class DetectionWorker:
    """Polls a frame buffer and runs detection whenever the frame changes."""

    def __init__(
        self,
        camera_name: str,
        buffer: DoubleFrameBuffer,
        backend: DetectionBackend,
        allowed_classes: frozenset[str],
        interval_seconds: float,
        on_result: Callable[[DetectionSnapshot], None],
    ) -> None:
        self._camera_name = camera_name
        self._buffer = buffer
        self._backend = backend
        self._allowed_classes = allowed_classes
        self._interval_seconds = interval_seconds
        self._on_result = on_result
        self._last_sequence: int | None = None

    async def run(self) -> None:
        """Run the polling loop until cancelled.

        Sleeps for the configured interval, then checks the buffer for a new
        frame. Detection errors are logged and skipped so a single bad frame
        never kills the loop. Cancellation during inference is propagated
        without invoking the result callback.
        """
        loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                snapshot = await self._process_next_frame(loop)
                if snapshot is not None:
                    self._on_result(snapshot)
        except asyncio.CancelledError:
            raise

    async def _process_next_frame(
        self, loop: asyncio.AbstractEventLoop
    ) -> DetectionSnapshot | None:
        frame = self._buffer.read()
        if frame is None or frame.sequence == self._last_sequence:
            return None

        try:
            detections = await loop.run_in_executor(
                None, self._backend.detect, frame.image, self._allowed_classes
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Detection failed for camera %r on frame %d",
                self._camera_name,
                frame.sequence,
            )
            return None

        self._last_sequence = frame.sequence
        return DetectionSnapshot(detections=detections, sequence=frame.sequence)
