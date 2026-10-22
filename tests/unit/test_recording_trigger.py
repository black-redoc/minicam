"""Tests for RecordingTrigger: confirmation, post-roll deadline, class history."""

from __future__ import annotations

from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot
from app.services.recording.trigger import RecordingTrigger


def _snapshot(*classes: str) -> DetectionSnapshot:
    return DetectionSnapshot(
        detections=[Detection(c, 0.9, 0, 0, 10, 10) for c in classes], sequence=0
    )


def test_single_detection_does_not_start_recording() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0)
    assert trigger.on_snapshot(_snapshot("person"), now=1.0) is None


def test_two_consecutive_detections_start_recording() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0)
    trigger.on_snapshot(_snapshot("person"), now=1.0)
    assert trigger.on_snapshot(_snapshot("person"), now=1.3) == 6.3


def test_empty_snapshot_resets_the_confirmation_streak() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0)
    trigger.on_snapshot(_snapshot("person"), now=1.0)
    assert trigger.on_snapshot(_snapshot(), now=1.3) is None
    assert trigger.on_snapshot(_snapshot("person"), now=1.6) is None


def test_while_recording_a_single_detection_extends_the_deadline() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0)
    trigger.on_snapshot(_snapshot("person"), now=1.0)
    trigger.on_snapshot(_snapshot("person"), now=1.3)
    trigger.on_snapshot(_snapshot(), now=3.0)
    assert trigger.on_snapshot(_snapshot("dog"), now=4.0) == 9.0


def test_after_the_deadline_detections_need_confirming_again() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0)
    trigger.on_snapshot(_snapshot("person"), now=1.0)
    trigger.on_snapshot(_snapshot("person"), now=1.3)
    trigger.on_snapshot(_snapshot(), now=2.0)
    assert trigger.on_snapshot(_snapshot("person"), now=10.0) is None
    assert trigger.on_snapshot(_snapshot("person"), now=10.3) == 15.3


def test_classes_between_unions_the_classes_seen_in_the_window() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0)
    trigger.on_snapshot(_snapshot("person"), now=1.0)
    trigger.on_snapshot(_snapshot("person", "dog"), now=1.3)
    trigger.on_snapshot(_snapshot("car"), now=2.0)
    trigger.on_snapshot(_snapshot(), now=3.0)
    trigger.on_snapshot(_snapshot("cat"), now=30.0)  # unconfirmed: not recorded
    assert trigger.classes_between(0.0, 10.0) == ("car", "dog", "person")
    assert trigger.classes_between(20.0, 40.0) == ()


def test_history_is_bounded() -> None:
    trigger = RecordingTrigger(post_roll_seconds=5.0, history_seconds=10.0)
    trigger.on_snapshot(_snapshot("person"), now=1.0)
    trigger.on_snapshot(_snapshot("person"), now=1.3)
    for t in range(2, 30):
        trigger.on_snapshot(_snapshot("dog"), now=float(t))
    assert trigger.classes_between(0.0, 100.0) == ("dog",)
