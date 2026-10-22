"""Decides, from detection snapshots, until when a camera should record.

A recording starts once detections appear in a few consecutive snapshots
(the same single-frame false-positive filter as notifications) and keeps
going until POST_ROLL_SECONDS after the last snapshot with detections. The
result is a time.monotonic() deadline handed to the camera's recorder, so a
stalled detection worker simply lets the deadline pass and the clip ends.

It also remembers which classes were seen when, so a finished clip can be
labelled with what triggered it.
"""

from __future__ import annotations

import math
from collections import deque

from app.services.detection.worker import DetectionSnapshot

POST_ROLL_SECONDS = 5.0
_CONFIRMATION_SNAPSHOTS = 2
# Long enough to cover the longest clip (see recorder.MAX_CLIP_SECONDS)
# plus its pre/post-roll.
_HISTORY_SECONDS = 600.0


class RecordingTrigger:
    """Per-camera detection state for event recording."""

    def __init__(
        self,
        post_roll_seconds: float = POST_ROLL_SECONDS,
        confirmation_snapshots: int = _CONFIRMATION_SNAPSHOTS,
        history_seconds: float = _HISTORY_SECONDS,
    ) -> None:
        self._post_roll_seconds = post_roll_seconds
        self._confirmation_snapshots = confirmation_snapshots
        self._history_seconds = history_seconds
        self._streak = 0
        self._deadline = -math.inf
        self._history: deque[tuple[float, frozenset[str]]] = deque()

    def on_snapshot(self, snapshot: DetectionSnapshot, now: float) -> float | None:
        """Return the new recording deadline, or None if it is unchanged."""
        if not snapshot.detections:
            self._streak = 0
            return None

        self._streak += 1
        recording = now < self._deadline
        if self._streak < self._confirmation_snapshots and not recording:
            return None

        classes = frozenset(d.class_name for d in snapshot.detections)
        self._history.append((now, classes))
        while self._history and self._history[0][0] < now - self._history_seconds:
            self._history.popleft()
        self._deadline = now + self._post_roll_seconds
        return self._deadline

    def classes_between(self, start: float, end: float) -> tuple[str, ...]:
        """Sorted classes seen in [start, end] (time.monotonic() seconds)."""
        seen: set[str] = set()
        for at, classes in self._history:
            if start <= at <= end:
                seen |= classes
        return tuple(sorted(seen))
