import numpy as np
import pytest

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.models.frame import Frame


@pytest.fixture
def sample_image() -> np.ndarray:
    """Return a blank 640x480 RGB24 numpy array."""
    return np.zeros((480, 640, 3), dtype=np.uint8)


@pytest.fixture
def sample_frame(sample_image: np.ndarray) -> Frame:
    """Return a Frame with default test values."""
    return Frame(
        image=sample_image,
        width=640,
        height=480,
        timestamp=1.0,
        sequence=0,
    )


@pytest.fixture
def frame_buffer() -> DoubleFrameBuffer:
    """Return an empty DoubleFrameBuffer."""
    return DoubleFrameBuffer()
