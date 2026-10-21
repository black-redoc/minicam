"""Per-camera JPEG encoding shared by every viewer of that camera."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.models.frame import Frame


class SharedJpegEncoder:
    """Encodes each frame at most once, however many viewers request it.

    The first viewer to ask for a frame starts the encode in the default
    executor; viewers asking for the same frame meanwhile (or afterwards)
    await that same future. Encoding CPU therefore scales with the camera's
    frame rate, not with viewer count × frame rate.

    The future is shielded so one viewer disconnecting mid-encode cannot
    cancel the work the other viewers are waiting on. Must be used from a
    single event loop.
    """

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._jpeg: asyncio.Future[bytes] | None = None

    async def encode(self, frame: Frame, render: Callable[[Frame], bytes]) -> bytes:
        """Return the JPEG for frame, running render(frame) only if needed.

        Args:
            frame: The frame to encode; compared by identity with the
                previous request.
            render: CPU-bound frame -> JPEG function, run off the event loop.
        """
        if self._jpeg is None or frame is not self._frame:
            loop = asyncio.get_running_loop()
            self._frame = frame
            self._jpeg = loop.run_in_executor(None, render, frame)
            # Mark a failure as retrieved even if every viewer left before it
            # finished, so asyncio does not log "exception never retrieved".
            self._jpeg.add_done_callback(_consume_exception)
        return await asyncio.shield(self._jpeg)


def _consume_exception(future: asyncio.Future[bytes]) -> None:
    if not future.cancelled():
        future.exception()
