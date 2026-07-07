from __future__ import annotations

import time

import av
import numpy as np

from app.models.frame import Frame


def decode_video_frame(av_frame: av.VideoFrame, sequence: int) -> Frame:
    """Convert a PyAV VideoFrame to an immutable Frame.

    Reformats the frame to RGB24 and wraps it in the domain model.
    This function is pure: same inputs always produce equivalent outputs.

    Args:
        av_frame: The PyAV video frame to decode.
        sequence: Monotonically increasing sequence number assigned by the caller.

    Returns:
        An immutable Frame with a read-only RGB24 numpy array.
    """
    rgb_frame = av_frame.reformat(format="rgb24")
    image: np.ndarray = rgb_frame.to_ndarray()

    if av_frame.pts is not None and av_frame.time_base is not None:
        timestamp = float(av_frame.pts * av_frame.time_base)
    else:
        timestamp = time.monotonic()

    return Frame(
        image=image,
        width=av_frame.width,
        height=av_frame.height,
        timestamp=timestamp,
        sequence=sequence,
    )
