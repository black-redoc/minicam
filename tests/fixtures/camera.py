from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from app.models.frame import Frame


def make_mock_av_frame(
    width: int = 640,
    height: int = 480,
    pts: int | None = 1000,
    time_base: float = 1 / 90000,
) -> MagicMock:
    """Create a MagicMock that mimics av.VideoFrame for use in unit tests."""
    mock_frame = MagicMock()
    mock_frame.width = width
    mock_frame.height = height
    mock_frame.pts = pts
    mock_frame.time_base = time_base

    rgb_array = np.zeros((height, width, 3), dtype=np.uint8)
    mock_rgb = MagicMock()
    mock_rgb.to_ndarray.return_value = rgb_array
    mock_frame.reformat.return_value = mock_rgb

    return mock_frame


def make_frame(
    sequence: int = 0,
    width: int = 640,
    height: int = 480,
) -> Frame:
    """Create a Frame instance with sensible defaults for use in unit tests."""
    return Frame(
        image=np.zeros((height, width, 3), dtype=np.uint8),
        width=width,
        height=height,
        timestamp=float(sequence) * 0.033,
        sequence=sequence,
    )
