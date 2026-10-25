from __future__ import annotations

import time

import av
import numpy as np

from app.models.frame import Frame


def decode_video_frame(av_frame: av.VideoFrame, sequence: int) -> Frame:
    """Wrap a PyAV VideoFrame in an immutable Frame.

    The RGB24 conversion is deferred to the first access to Frame.image:
    most frames are overwritten in the buffer before anything reads them.

    Args:
        av_frame: The PyAV video frame to decode.
        sequence: Monotonically increasing sequence number assigned by the caller.

    Returns:
        An immutable Frame whose image is a read-only RGB24 numpy array.
    """
    if av_frame.pts is not None and av_frame.time_base is not None:
        timestamp = float(av_frame.pts * av_frame.time_base)
    else:
        timestamp = time.monotonic()

    def to_rgb() -> np.ndarray:
        return av_frame.reformat(format="rgb24").to_ndarray()

    return Frame.lazy(
        to_rgb,
        width=av_frame.width,
        height=av_frame.height,
        timestamp=timestamp,
        sequence=sequence,
    )
