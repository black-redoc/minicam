"""Tests for app.services.detection.draw."""

from __future__ import annotations

import numpy as np

from app.services.detection.base import Detection
from app.services.detection.draw import (
    BORDER_THICKNESS_PX,
    color_for_class,
    draw_detections,
)

_HEIGHT = 40
_WIDTH = 60


def _blank_image() -> np.ndarray:
    image = np.zeros((_HEIGHT, _WIDTH, 3), dtype=np.uint8)
    image.flags.writeable = False
    return image


def _make_detection(
    class_name: str = "person", x1: int = 10, y1: int = 10, x2: int = 30, y2: int = 30
) -> Detection:
    return Detection(class_name=class_name, confidence=0.9, x1=x1, y1=y1, x2=x2, y2=y2)


class TestDrawDetectionsNoDetections:
    def test_returns_image_unchanged_when_no_detections(self) -> None:
        image = _blank_image()
        result = draw_detections(image, [])
        assert np.array_equal(result, image)


class TestDrawDetectionsSingleBox:
    def test_border_pixels_change_and_interior_stays_untouched(self) -> None:
        image = _blank_image()
        detection = _make_detection(x1=10, y1=10, x2=30, y2=30)

        result = draw_detections(image, [detection])
        color = color_for_class(detection.class_name)

        # Border pixels (just inside the rectangle edges) got the color.
        assert tuple(result[10, 20]) == color  # top edge
        assert tuple(result[30, 20]) == color  # bottom edge
        assert tuple(result[20, 10]) == color  # left edge
        assert tuple(result[20, 30]) == color  # right edge

        # Interior pixels, past the border thickness, are untouched.
        interior_y = 10 + BORDER_THICKNESS_PX + 2
        interior_x = 10 + BORDER_THICKNESS_PX + 2
        assert tuple(result[interior_y, interior_x]) == (0, 0, 0)

    def test_does_not_mutate_original_image(self) -> None:
        image = _blank_image()
        original_copy = image.copy()
        detection = _make_detection()

        draw_detections(image, [detection])

        assert np.array_equal(image, original_copy)

    def test_out_of_bounds_coordinates_are_clipped_without_raising(self) -> None:
        image = _blank_image()
        detection = _make_detection(x1=-100, y1=-100, x2=_WIDTH + 100, y2=_HEIGHT + 100)

        result = draw_detections(image, [detection])

        color = color_for_class(detection.class_name)
        assert tuple(result[0, 0]) == color
        assert tuple(result[_HEIGHT - 1, _WIDTH - 1]) == color


class TestDrawDetectionsMultipleClasses:
    def test_different_classes_get_different_border_colors(self) -> None:
        image = _blank_image()
        person = _make_detection(class_name="person", x1=0, y1=0, x2=10, y2=10)
        dog = _make_detection(class_name="dog", x1=40, y1=20, x2=59, y2=39)

        result = draw_detections(image, [person, dog])

        person_color = tuple(result[0, 5])
        dog_color = tuple(result[20, 50])
        assert person_color != dog_color
        assert person_color == color_for_class("person")
        assert dog_color == color_for_class("dog")


class TestColorForClass:
    def test_same_class_name_always_returns_same_color(self) -> None:
        assert color_for_class("cat") == color_for_class("cat")

    def test_unknown_classes_get_a_deterministic_fallback_color(self) -> None:
        assert color_for_class("skateboard") == color_for_class("skateboard")
        # Two different unknown classes are not guaranteed to differ (the
        # fallback palette is finite), but person's dedicated color must
        # never collide with the generic fallback used for other classes.
        assert color_for_class("skateboard") != color_for_class("person")
