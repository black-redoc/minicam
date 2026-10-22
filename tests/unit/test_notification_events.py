"""Tests for DetectionEvent payloads and the SSE EventHub."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.notifications.events import DetectionEvent, EventHub

_EVENT = DetectionEvent(
    "Entrance", ("dog", "person"), datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
)


def test_payload_describes_the_detection() -> None:
    assert _EVENT.to_payload() == {
        "type": "detection",
        "camera": "Entrance",
        "classes": ["dog", "person"],
        "detected_at": "2026-10-21T12:00:00+00:00",
        "title": "minicam",
        "body": "dog, person detected on Entrance",
    }


async def test_publish_reaches_every_subscriber() -> None:
    hub = EventHub()
    with hub.subscribe() as first, hub.subscribe() as second:
        hub.publish(_EVENT)
        assert first.get_nowait() == _EVENT
        assert second.get_nowait() == _EVENT


async def test_unsubscribes_on_exit() -> None:
    hub = EventHub()
    with hub.subscribe():
        assert hub.subscriber_count == 1
    assert hub.subscriber_count == 0
    hub.publish(_EVENT)  # no subscribers: must not raise


async def test_full_queue_drops_events_without_blocking_others() -> None:
    hub = EventHub(queue_size=1)
    with hub.subscribe() as slow, hub.subscribe() as fast:
        hub.publish(_EVENT)
        hub.publish(_EVENT)
        assert slow.qsize() == 1
        fast.get_nowait()
        assert fast.empty()
