"""Draw detection bounding boxes on frames using pure numpy.

Pillow and OpenCV are deliberately not used here. app/services/encoder.py
already documents that Pillow was ruled out for the encoding path because
of the latency of a numpy -> PIL.Image -> numpy round-trip; drawing runs
in that exact same per-frame hot path (right before encode_jpeg), so the
same constraint applies. Box borders are instead drawn by assigning pixel
values directly via numpy slicing on the four edge strips of each
rectangle — no new dependency, no format conversion.

Text labels are out of scope for this phase (see the detection plan):
there is no cheap way to rasterize text without Pillow/OpenCV, so the
border color alone identifies the class.
"""

from __future__ import annotations

import zlib

import numpy as np

from app.services.detection.base import Detection

BORDER_THICKNESS_PX = 3

# Small fixed palette, RGB tuples (matches the RGB24 frame format).
# "person" gets an explicit entry since it's the primary use case called
# out in the plan; every other class name — including all remaining COCO
# classes and any class a future backend adds — falls back to a color
# picked deterministically from _FALLBACK_COLORS by _stable_hash(), so
# this dict never needs to grow to cover 80+ classes by hand.
_CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "person": (255, 0, 0),
}
_FALLBACK_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 200, 0),
    (0, 120, 255),
    (255, 200, 0),
    (200, 0, 255),
    (0, 220, 220),
    (255, 120, 0),
    (140, 0, 255),
    (255, 0, 160),
)


def _stable_hash(class_name: str) -> int:
    """Deterministic hash of a class name, stable across processes.

    Python's built-in hash() for str is salted per-process
    (PYTHONHASHSEED) by design, which would make a class's fallback color
    change every restart. crc32 has no such salting, so the same class
    name always maps to the same palette index everywhere.
    """
    return zlib.crc32(class_name.encode("utf-8"))


def color_for_class(class_name: str) -> tuple[int, int, int]:
    """Return a deterministic RGB color for a detection's class name."""
    explicit_color = _CLASS_COLORS.get(class_name)
    if explicit_color is not None:
        return explicit_color
    index = _stable_hash(class_name) % len(_FALLBACK_COLORS)
    return _FALLBACK_COLORS[index]


def draw_detections(image: np.ndarray, detections: list[Detection]) -> np.ndarray:
    """Return a copy of image with each detection's box border drawn.

    image is never mutated in place: frames handed to this function are
    read-only (see app/models/frame.py), so writing to them directly would
    raise ValueError, and mutating a frame shared across viewers would
    corrupt every other viewer's output anyway. Always returning a fresh
    copy keeps the function's contract simple and safe to call
    unconditionally; the caller (app/routers/stream.py) is the one that
    skips calling this entirely when detections is empty, to avoid paying
    for the copy on the hot path when there is nothing to draw.

    Args:
        image: RGB24 array of shape (height, width, 3).
        detections: Boxes to draw, in absolute pixel coordinates. May
            contain coordinates outside the image bounds; they are
            clipped before use.

    Returns:
        A new array with box borders drawn. The input array is untouched.
    """
    output = image.copy()
    height, width = output.shape[0], output.shape[1]

    for detection in detections:
        _draw_box_border(output, detection, height, width)

    return output


def _draw_box_border(
    output: np.ndarray, detection: Detection, height: int, width: int
) -> None:
    """Draw one detection's box border directly onto output, in place.

    This is a presentation-layer function and must not assume its input
    is already valid, even though upstream code (onnx_backend's
    _undo_letterbox) already clips coordinates today — a drawing function
    should never be able to throw IndexError on out-of-range input.
    """
    x1 = min(max(detection.x1, 0), width - 1)
    x2 = min(max(detection.x2, 0), width - 1)
    y1 = min(max(detection.y1, 0), height - 1)
    y2 = min(max(detection.y2, 0), height - 1)
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)

    color = color_for_class(detection.class_name)
    # Cap the border thickness for boxes smaller than 2x the default
    # thickness, so the top/bottom (or left/right) strips never overlap
    # past the box's own extent.
    thickness_y = min(BORDER_THICKNESS_PX, (y2 - y1) // 2 + 1)
    thickness_x = min(BORDER_THICKNESS_PX, (x2 - x1) // 2 + 1)

    output[y1 : y1 + thickness_y, x1 : x2 + 1] = color
    output[y2 - thickness_y + 1 : y2 + 1, x1 : x2 + 1] = color
    output[y1 : y2 + 1, x1 : x1 + thickness_x] = color
    output[y1 : y2 + 1, x2 - thickness_x + 1 : x2 + 1] = color
