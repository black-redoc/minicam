"""Core abstractions for the object detection engine.

Defines the detection result shape and the backend interface every
inference engine must implement. Kept intentionally small so a future
accelerator backend (e.g. Hailo) can be swapped in without touching the
rest of the system — only `app.services.detection.factory` needs to know
which concrete backend is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True, slots=True)
class Detection:
    """A single detected object within a frame, in image-space coordinates."""

    class_name: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int


class DetectionBackend(Protocol):
    """Interface every object-detection inference engine must implement."""

    @property
    def available_classes(self) -> tuple[str, ...]:
        """Class names this backend can recognize, in model output order."""
        ...

    def detect(
        self, image: np.ndarray, allowed_classes: frozenset[str]
    ) -> list[Detection]:
        """Run inference on a single frame and return matching detections.

        Args:
            image: Array of shape (height, width, 3) in RGB24 format.
            allowed_classes: Only detections whose class name is in this set
                are returned; an empty set yields an empty result.

        Returns:
            Detections above the backend's confidence threshold, filtered to
            allowed_classes.
        """
        ...
