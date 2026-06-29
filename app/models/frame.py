from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, eq=False)
class Frame:
    """Immutable decoded video frame.

    Args:
        image: RGB24 numpy array of shape (height, width, 3).
        width: Frame width in pixels.
        height: Frame height in pixels.
        timestamp: Frame timestamp in seconds.
        sequence: Monotonically increasing frame sequence number.
    """

    image: np.ndarray
    width: int
    height: int
    timestamp: float
    sequence: int

    def __post_init__(self) -> None:
        # Prevent callers from mutating the underlying array after construction.
        # frozen=True only protects the reference, not the array contents.
        self.image.flags.writeable = False
