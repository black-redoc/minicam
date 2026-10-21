from __future__ import annotations

import asyncio
import logging

from app.models.frame import Frame

logger = logging.getLogger(__name__)


class FrameBuffer:
    """Latest-frame slot for one producer thread and many async readers.

    write() is called from the camera's capture thread and simply replaces
    the stored reference: a single attribute assignment is atomic under the
    GIL, so neither a lock nor a hand-off thread is needed. read() is the
    matching lock-free snapshot used by pollers (the detection worker).

    Async readers (WebSocket streams) use next_frame() instead of polling:
    every write schedules a wake-up on the event loop that first awaited,
    via call_soon_threadsafe, so readers sleep until a new frame exists.

    If free-threaded Python (3.13+ no-GIL) is ever used, add explicit
    synchronization around _latest.
    """

    def __init__(self) -> None:
        self._latest: Frame | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._new_frame: asyncio.Event | None = None

    def write(self, frame: Frame) -> None:
        """Publish frame as the latest one and wake any waiting readers.

        Args:
            frame: The decoded frame to store.
        """
        self._latest = frame
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._notify)
            except RuntimeError:
                # Loop already closed (shutdown): nobody left to wake.
                pass

    def read(self) -> Frame | None:
        """Return the latest available frame, or None if none written yet."""
        return self._latest

    async def next_frame(self, after: Frame | None = None) -> Frame:
        """Wait until the latest frame is not `after`, then return it.

        Frames are compared by identity, so a reader never receives the
        same frame twice and intermediate frames it was too slow for are
        skipped. Must always be awaited from the same event loop.

        Args:
            after: The last frame this reader already handled, if any.
        """
        while True:
            frame = self._latest
            if frame is not None and frame is not after:
                return frame
            # No await between the check above and grabbing the event, so a
            # concurrent write's _notify (which runs on this same loop) can
            # never slip in between and be missed.
            await self._bind_event().wait()

    def shutdown(self) -> None:
        """Stop scheduling wake-ups; called once the camera is torn down."""
        self._loop = None

    def _bind_event(self) -> asyncio.Event:
        if self._new_frame is None:
            self._loop = asyncio.get_running_loop()
            self._new_frame = asyncio.Event()
        return self._new_frame

    def _notify(self) -> None:
        """Wake every current waiter; later waiters wait on a fresh event."""
        event = self._new_frame
        if event is not None:
            self._new_frame = asyncio.Event()
            event.set()
