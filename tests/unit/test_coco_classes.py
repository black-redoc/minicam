"""Tests for app.services.detection.coco_classes."""

from __future__ import annotations

from app.services.detection.coco_classes import COCO_CLASSES


class TestCocoClasses:
    def test_has_eighty_classes(self) -> None:
        assert len(COCO_CLASSES) == 80

    def test_is_a_tuple(self) -> None:
        assert isinstance(COCO_CLASSES, tuple)

    def test_all_names_are_unique(self) -> None:
        assert len(set(COCO_CLASSES)) == len(COCO_CLASSES)

    def test_first_class_is_person(self) -> None:
        assert COCO_CLASSES[0] == "person"

    def test_last_class_is_toothbrush(self) -> None:
        assert COCO_CLASSES[-1] == "toothbrush"

    def test_contains_expected_animal_classes(self) -> None:
        for name in ("cat", "dog", "bird", "horse", "cow"):
            assert name in COCO_CLASSES

    def test_all_names_are_lowercase_strings(self) -> None:
        assert all(
            isinstance(name, str) and name == name.lower() for name in COCO_CLASSES
        )
