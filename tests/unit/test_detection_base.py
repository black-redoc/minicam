"""Tests for app.services.detection.base."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.services.detection.base import Detection


def _make_detection(**overrides: object) -> Detection:
    defaults: dict[str, object] = {
        "class_name": "person",
        "confidence": 0.9,
        "x1": 10,
        "y1": 20,
        "x2": 30,
        "y2": 40,
    }
    defaults.update(overrides)
    return Detection(**defaults)  # type: ignore[arg-type]


class TestDetection:
    def test_stores_fields(self) -> None:
        detection = _make_detection()
        assert detection.class_name == "person"
        assert detection.confidence == 0.9
        assert (detection.x1, detection.y1, detection.x2, detection.y2) == (
            10,
            20,
            30,
            40,
        )

    def test_is_frozen(self) -> None:
        detection = _make_detection()
        with pytest.raises(dataclasses.FrozenInstanceError):
            detection.confidence = 0.1  # type: ignore[misc]

    def test_has_no_dict_due_to_slots(self) -> None:
        detection = _make_detection()
        assert not hasattr(detection, "__dict__")

    def test_equality_by_value(self) -> None:
        assert _make_detection() == _make_detection()

    def test_inequality_on_different_class(self) -> None:
        assert _make_detection(class_name="cat") != _make_detection(class_name="dog")


class _FakeBackend:
    """Minimal DetectionBackend implementation used to check the Protocol shape."""

    @property
    def available_classes(self) -> tuple[str, ...]:
        return ("person", "cat")

    def detect(
        self, image: np.ndarray, allowed_classes: frozenset[str]
    ) -> list[Detection]:
        if "person" not in allowed_classes:
            return []
        return [_make_detection()]


class TestDetectionBackendProtocol:
    def test_fake_backend_exposes_available_classes(self) -> None:
        backend = _FakeBackend()
        assert backend.available_classes == ("person", "cat")

    def test_fake_backend_detect_respects_allowed_classes(self) -> None:
        backend = _FakeBackend()
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        assert backend.detect(image, frozenset()) == []
        assert backend.detect(image, frozenset({"person"})) == [_make_detection()]
