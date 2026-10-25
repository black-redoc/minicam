from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np


class Frame:
    """Immutable decoded video frame.

    The RGB image can be given up front (``image``) or produced on first
    access to ``Frame.image`` (``Frame.lazy``). Most frames are replaced in
    the buffer before anyone reads their pixels, so lazy frames skip the
    YUV-to-RGB conversion for them.

    Args:
        image: RGB24 numpy array of shape (height, width, 3).
        width: Frame width in pixels.
        height: Frame height in pixels.
        timestamp: Frame timestamp in seconds.
        sequence: Monotonically increasing frame sequence number.
    """

    __slots__ = (
        "_convert",
        "_height",
        "_image",
        "_lock",
        "_sequence",
        "_timestamp",
        "_width",
    )

    def __init__(
        self,
        image: np.ndarray,
        width: int,
        height: int,
        timestamp: float,
        sequence: int,
    ) -> None:
        self._init(width, height, timestamp, sequence)
        self._image: np.ndarray | None = _read_only(image)
        self._convert: Callable[[], np.ndarray] | None = None

    @classmethod
    def lazy(
        cls,
        convert: Callable[[], np.ndarray],
        width: int,
        height: int,
        timestamp: float,
        sequence: int,
    ) -> Frame:
        """Build a frame whose image is produced by `convert` on first access.

        `convert` runs at most once, even with concurrent readers, and is
        dropped afterwards so whatever it captured can be freed.
        """
        frame = cls.__new__(cls)
        frame._init(width, height, timestamp, sequence)
        frame._image = None
        frame._convert = convert
        return frame

    def _init(self, width: int, height: int, timestamp: float, sequence: int) -> None:
        self._width = width
        self._height = height
        self._timestamp = timestamp
        self._sequence = sequence
        self._lock = threading.Lock()

    @property
    def image(self) -> np.ndarray:
        """Read-only RGB24 array of shape (height, width, 3)."""
        image = self._image
        if image is not None:
            return image
        with self._lock:
            if self._image is None:
                assert self._convert is not None
                self._image = _read_only(self._convert())
                self._convert = None
            return self._image

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def timestamp(self) -> float:
        return self._timestamp

    @property
    def sequence(self) -> int:
        return self._sequence


def _read_only(image: np.ndarray) -> np.ndarray:
    # Prevent callers from mutating the array after construction: an
    # immutable reference alone does not protect the array contents.
    image.flags.writeable = False
    return image
