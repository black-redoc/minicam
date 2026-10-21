from collections.abc import Generator

import numpy as np
import pytest

from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import get_login_rate_limiter
from app.models.frame import Frame


@pytest.fixture(autouse=True)
def reset_login_rate_limiter() -> Generator[None]:
    """Give every test a fresh LoginRateLimiter.

    get_login_rate_limiter is a process-wide lru_cache singleton (by design
    — the limiter must share state across requests within the app). Without
    this reset, unrelated tests hitting /api/auth/login with the same
    email+IP combination would silently share a rate-limit window and
    intermittently 429 each other out.
    """
    get_login_rate_limiter.cache_clear()
    yield
    get_login_rate_limiter.cache_clear()


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
def frame_buffer() -> FrameBuffer:
    """Return an empty FrameBuffer."""
    return FrameBuffer()
