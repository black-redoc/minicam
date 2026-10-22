"""Detection notification events and the in-process hub that fans them out.

The hub is what Server-Sent Events subscribers listen on: one bounded queue
per open browser connection. A slow or stalled client never blocks the
detection path — when its queue is full, new events for it are dropped.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_SUBSCRIBER_QUEUE_SIZE = 16


@dataclass(frozen=True, slots=True)
class DetectionEvent:
    """Objects of the selected classes were detected on one camera."""

    camera_name: str
    classes: tuple[str, ...]
    detected_at: datetime

    @property
    def message(self) -> str:
        return f"{', '.join(self.classes)} detected on {self.camera_name}"

    def to_payload(self) -> dict[str, object]:
        return {
            "type": "detection",
            "camera": self.camera_name,
            "classes": list(self.classes),
            "detected_at": self.detected_at.astimezone(UTC).isoformat(),
            "title": "minicam",
            "body": self.message,
        }


class EventHub:
    """Fans each published event out to every current subscriber."""

    def __init__(self, queue_size: int = _SUBSCRIBER_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[DetectionEvent]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[DetectionEvent]]:
        queue: asyncio.Queue[DetectionEvent] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, event: DetectionEvent) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug(
                    "Dropping detection event for a slow subscriber: camera=%s",
                    event.camera_name,
                )
