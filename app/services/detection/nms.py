"""Greedy non-maximum suppression, vectorized with plain numpy.

No opencv, no Pillow — this stays a pure-numpy dependency like the rest of
the detection package (see app/services/detection/onnx_backend.py for why).
"""

from __future__ import annotations

import numpy as np

DEFAULT_IOU_THRESHOLD = 0.45


def non_max_suppression(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> np.ndarray:
    """Greedily keep the highest-scoring boxes, dropping heavy overlaps.

    Args:
        boxes: Array of shape (N, 4) with (x1, y1, x2, y2) per row.
        scores: Array of shape (N,) with a confidence score per box.
        iou_threshold: Boxes overlapping a kept box by more than this IoU
            fraction are suppressed.

    Returns:
        Indices into the original `boxes`/`scores` arrays to keep, ordered
        by descending score.
    """
    if boxes.shape[0] == 0:
        return np.empty(0, dtype=np.int64)

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)

    order = np.argsort(scores)[::-1]
    keep: list[int] = []

    while order.size > 0:
        current = order[0]
        keep.append(int(current))
        rest = order[1:]
        if rest.size == 0:
            break

        iou = _iou_against(current, rest, x1, y1, x2, y2, areas)
        order = rest[iou <= iou_threshold]

    return np.array(keep, dtype=np.int64)


def _iou_against(
    current: np.intp,
    rest: np.ndarray,
    x1: np.ndarray,
    y1: np.ndarray,
    x2: np.ndarray,
    y2: np.ndarray,
    areas: np.ndarray,
) -> np.ndarray:
    """Vectorized IoU of the `current` box against every box in `rest`."""
    xx1 = np.maximum(x1[current], x1[rest])
    yy1 = np.maximum(y1[current], y1[rest])
    xx2 = np.minimum(x2[current], x2[rest])
    yy2 = np.minimum(y2[current], y2[rest])

    inter_w = np.maximum(0.0, xx2 - xx1)
    inter_h = np.maximum(0.0, yy2 - yy1)
    intersection = inter_w * inter_h
    union = areas[current] + areas[rest] - intersection

    return np.where(union > 0, intersection / union, 0.0)
