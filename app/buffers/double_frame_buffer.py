from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from app.models.frame import Frame

logger = logging.getLogger(__name__)


class DoubleFrameBuffer:
    """Thread-safe double frame buffer for single-producer, multiple-reader use.

    Writes are submitted to a single-worker executor, which serializes them
    without an explicit lock. Reads are lock-free — CPython's GIL guarantees
    that a LOAD_ATTR on _read is atomic.

    If free-threaded Python (3.13+ no-GIL) is ever used, add explicit
    synchronization to read() as well.
    """

    def __init__(self) -> None:
        self._read: Frame | None = None
        self._write: Frame | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="frame_buffer",
        )

    def write(self, frame: Frame) -> None:
        """Submit a frame swap to the serialized executor.

        Args:
            frame: The decoded frame to store.
        """
        future = self._executor.submit(self._swap, frame)
        future.add_done_callback(self._log_if_failed)

    def read(self) -> Frame | None:
        """Return the latest available frame, or None if none written yet."""
        return self._read

    def shutdown(self) -> None:
        """Shut down the internal executor, discarding pending writes."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _swap(self, frame: Frame) -> None:
        self._write = frame
        self._read, self._write = self._write, self._read

    def _log_if_failed(self, future: Future[None]) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.error("Frame buffer swap failed", exc_info=exc)
