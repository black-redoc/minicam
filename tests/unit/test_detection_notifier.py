"""Tests for DetectionNotifier: confirmation, cooldown and per-camera switch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.config import CameraConfig, NotificationConfig
from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot
from app.services.notifications.events import DetectionEvent
from app.services.notifications.notifier import (
    DetectionNotifier,
    notifications_enabled_for,
)

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _snapshot(*class_names: str) -> DetectionSnapshot:
    return DetectionSnapshot(
        detections=[Detection(name, 0.9, 0, 0, 10, 10) for name in class_names],
        sequence=1,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _notifier(
    clock: _Clock,
    events: list[DetectionEvent],
    enabled: set[str] | None = None,
    cooldown: float = 60.0,
) -> DetectionNotifier:
    allowed = {"cam1"} if enabled is None else enabled
    return DetectionNotifier(
        is_enabled=lambda name: name in allowed,
        cooldown_seconds=lambda: cooldown,
        on_event=events.append,
        clock=clock,
        now=lambda: _NOW,
    )


class TestNotificationsEnabledFor:
    @pytest.mark.parametrize(
        ("mode", "global_enabled", "expected"),
        [
            ("inherit", True, True),
            ("inherit", False, False),
            ("on", False, True),
            ("on", True, True),
            ("off", True, False),
            ("off", False, False),
        ],
    )
    def test_resolves_override(
        self, mode: str, global_enabled: bool, expected: bool
    ) -> None:
        camera = CameraConfig(name="c", host="h", notifications=mode)  # type: ignore[arg-type]
        global_config = NotificationConfig(enabled=global_enabled)
        assert notifications_enabled_for(camera, global_config) is expected


class TestDetectionNotifier:
    def test_single_snapshot_is_not_enough(self) -> None:
        events: list[DetectionEvent] = []
        _notifier(_Clock(), events).on_snapshot("cam1", _snapshot("person"))
        assert events == []

    def test_fires_after_consecutive_detections(self) -> None:
        events: list[DetectionEvent] = []
        notifier = _notifier(_Clock(), events)
        notifier.on_snapshot("cam1", _snapshot("person"))
        notifier.on_snapshot("cam1", _snapshot("person", "dog", "person"))
        assert events == [DetectionEvent("cam1", ("dog", "person"), _NOW)]

    def test_empty_snapshot_resets_confirmation(self) -> None:
        events: list[DetectionEvent] = []
        notifier = _notifier(_Clock(), events)
        notifier.on_snapshot("cam1", _snapshot("person"))
        notifier.on_snapshot("cam1", _snapshot())
        notifier.on_snapshot("cam1", _snapshot("person"))
        assert events == []

    def test_cooldown_limits_to_one_event_per_window(self) -> None:
        clock = _Clock()
        events: list[DetectionEvent] = []
        notifier = _notifier(clock, events, cooldown=60)
        for _ in range(5):
            notifier.on_snapshot("cam1", _snapshot("person"))
        assert len(events) == 1

        clock.now += 59.9
        notifier.on_snapshot("cam1", _snapshot("person"))
        assert len(events) == 1

        clock.now += 0.1
        notifier.on_snapshot("cam1", _snapshot("person"))
        assert len(events) == 2

    def test_cooldown_is_per_camera(self) -> None:
        events: list[DetectionEvent] = []
        notifier = _notifier(_Clock(), events, enabled={"cam1", "cam2"})
        for name in ("cam1", "cam1", "cam2", "cam2"):
            notifier.on_snapshot(name, _snapshot("person"))
        assert [e.camera_name for e in events] == ["cam1", "cam2"]

    def test_disabled_camera_never_fires(self) -> None:
        events: list[DetectionEvent] = []
        notifier = _notifier(_Clock(), events, enabled=set())
        for _ in range(3):
            notifier.on_snapshot("cam1", _snapshot("person"))
        assert events == []

    def test_disabled_camera_does_not_consume_cooldown(self) -> None:
        enabled: set[str] = set()
        events: list[DetectionEvent] = []
        notifier = _notifier(_Clock(), events, enabled=enabled)
        notifier.on_snapshot("cam1", _snapshot("person"))
        notifier.on_snapshot("cam1", _snapshot("person"))
        enabled.add("cam1")
        notifier.on_snapshot("cam1", _snapshot("person"))
        assert len(events) == 1

    def test_forget_resets_camera_state(self) -> None:
        events: list[DetectionEvent] = []
        notifier = _notifier(_Clock(), events)
        notifier.on_snapshot("cam1", _snapshot("person"))
        notifier.on_snapshot("cam1", _snapshot("person"))
        notifier.forget("cam1")
        notifier.on_snapshot("cam1", _snapshot("person"))
        notifier.on_snapshot("cam1", _snapshot("person"))
        assert len(events) == 2
