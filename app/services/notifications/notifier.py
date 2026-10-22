"""Turns the detection worker's snapshots into rate-limited notifications.

A worker reports a snapshot several times per second, so notifying on each
one would flood every device. An event fires only once a camera has had
detections in a few consecutive snapshots (filtering single-frame false
positives), and at most once per camera per cooldown window.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.models.config import CameraConfig, NotificationConfig
from app.services.detection.worker import DetectionSnapshot
from app.services.notifications.events import DetectionEvent

_CONFIRMATION_SNAPSHOTS = 2


def notifications_enabled_for(
    camera: CameraConfig, notifications: NotificationConfig
) -> bool:
    """Resolve a camera's notification override against the global switch."""
    if camera.notifications == "inherit":
        return notifications.enabled
    return camera.notifications == "on"


class DetectionNotifier:
    """Decides, per camera, when a detection snapshot becomes an event."""

    def __init__(
        self,
        is_enabled: Callable[[str], bool],
        cooldown_seconds: Callable[[], float],
        on_event: Callable[[DetectionEvent], None],
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        confirmation_snapshots: int = _CONFIRMATION_SNAPSHOTS,
    ) -> None:
        self._is_enabled = is_enabled
        self._cooldown_seconds = cooldown_seconds
        self._on_event = on_event
        self._clock = clock
        self._now = now
        self._confirmation_snapshots = confirmation_snapshots
        self._streaks: dict[str, int] = {}
        self._last_notified: dict[str, float] = {}

    def on_snapshot(self, camera_name: str, snapshot: DetectionSnapshot) -> None:
        if not snapshot.detections:
            self._streaks[camera_name] = 0
            return

        streak = self._streaks.get(camera_name, 0) + 1
        self._streaks[camera_name] = streak
        if streak < self._confirmation_snapshots or not self._is_enabled(camera_name):
            return

        now = self._clock()
        last = self._last_notified.get(camera_name)
        if last is not None and now - last < self._cooldown_seconds():
            return

        self._last_notified[camera_name] = now
        classes = tuple(sorted({d.class_name for d in snapshot.detections}))
        self._on_event(DetectionEvent(camera_name, classes, self._now()))

    def forget(self, camera_name: str) -> None:
        """Drop a camera's state (it was deleted or renamed)."""
        self._streaks.pop(camera_name, None)
        self._last_notified.pop(camera_name, None)
