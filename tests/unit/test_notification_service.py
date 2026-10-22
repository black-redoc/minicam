"""Tests for NotificationService: SSE publish and Web Push to camera viewers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiosqlite
import pytest

from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.push_subscription_repository import (
    SQLitePushSubscriptionRepository,
)
from app.repositories.user_repository import SQLiteUserRepository
from app.services.notifications.events import DetectionEvent, EventHub
from app.services.notifications.service import NotificationService
from app.services.notifications.web_push import PushResult

_EVENT = DetectionEvent("cam1", ("person",), datetime(2026, 10, 21, tzinfo=UTC))


class _FakeSender:
    def __init__(self, results: dict[str, PushResult] | None = None) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self.closed = False
        self._results = results or {}

    @property
    def public_key(self) -> str:
        return "pub"

    async def send(
        self, endpoint: str, p256dh: str, auth: str, payload: bytes
    ) -> PushResult:
        self.sent.append((endpoint, payload))
        return self._results.get(endpoint, PushResult.DELIVERED)

    async def aclose(self) -> None:
        self.closed = True


async def _seed(conn: aiosqlite.Connection) -> SQLitePushSubscriptionRepository:
    users = SQLiteUserRepository(conn)
    viewer = await users.create("viewer@example.com", "h", is_admin=False)
    outsider = await users.create("outsider@example.com", "h", is_admin=False)
    await SQLitePermissionRepository(conn).upsert(
        viewer.id, "cam1", can_view=True, can_use_ptz=False
    )
    subs = SQLitePushSubscriptionRepository(conn)
    await subs.upsert(viewer.id, "https://fcm.googleapis.com/viewer", "k", "a")
    await subs.upsert(viewer.id, "https://fcm.googleapis.com/gone", "k", "a")
    await subs.upsert(outsider.id, "https://fcm.googleapis.com/outsider", "k", "a")
    return subs


async def _drain(service: NotificationService) -> None:
    await service.aclose()


async def test_handle_publishes_to_hub_without_sender() -> None:
    hub = EventHub()
    service = NotificationService(hub, connection=lambda: None)  # type: ignore[arg-type,return-value]
    with hub.subscribe() as queue:
        service.handle(_EVENT)
        assert queue.get_nowait() == _EVENT
    await _drain(service)


async def test_pushes_only_to_camera_viewers_and_prunes_gone(
    db_connection: aiosqlite.Connection,
) -> None:
    subs = await _seed(db_connection)
    sender = _FakeSender({"https://fcm.googleapis.com/gone": PushResult.GONE})
    service = NotificationService(
        EventHub(),
        connection=lambda: db_connection,
        push_sender=sender,  # type: ignore[arg-type]
    )

    service.handle(_EVENT)
    await _drain(service)

    assert sorted(endpoint for endpoint, _ in sender.sent) == [
        "https://fcm.googleapis.com/gone",
        "https://fcm.googleapis.com/viewer",
    ]
    assert b'"body": "person detected on cam1"' in sender.sent[0][1]
    remaining = await subs.list_for_users([1, 2])
    assert {s.endpoint for s in remaining} == {
        "https://fcm.googleapis.com/viewer",
        "https://fcm.googleapis.com/outsider",
    }
    assert sender.closed


async def test_push_failure_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> aiosqlite.Connection:
        raise RuntimeError("db down")

    service = NotificationService(
        EventHub(),
        connection=broken,
        push_sender=_FakeSender(),  # type: ignore[arg-type]
    )
    service.handle(_EVENT)
    await _drain(service)
    assert "Web Push delivery failed" in caplog.text


async def test_sender_removed_before_task_runs_skips_push() -> None:
    sender = _FakeSender()
    service = NotificationService(
        EventHub(),
        connection=lambda: None,  # type: ignore[arg-type,return-value]
        push_sender=sender,  # type: ignore[arg-type]
    )
    service.handle(_EVENT)
    service.set_push_sender(None)
    await asyncio.sleep(0)
    await _drain(service)
    assert sender.sent == []
