"""Delivers detection events to browsers: live over SSE, offline via Web Push.

Recipients are resolved at send time from camera_permissions, so a user
only ever hears about cameras they are currently allowed to view — the same
deny-by-default rule as the dashboard and the stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import aiosqlite

from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.push_subscription_repository import (
    SQLitePushSubscriptionRepository,
)
from app.services.notifications.events import DetectionEvent, EventHub
from app.services.notifications.web_push import PushResult, WebPushSender

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        hub: EventHub,
        connection: Callable[[], aiosqlite.Connection],
        push_sender: WebPushSender | None = None,
    ) -> None:
        self._hub = hub
        self._connection = connection
        self._push_sender = push_sender
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def push_sender(self) -> WebPushSender | None:
        return self._push_sender

    def set_push_sender(self, push_sender: WebPushSender | None) -> None:
        self._push_sender = push_sender

    def handle(self, event: DetectionEvent) -> None:
        """Publish an event; called synchronously from the detection worker."""
        logger.info("Detection notification: %s", event.message)
        self._hub.publish(event)
        if self._push_sender is None:
            return
        task = asyncio.get_running_loop().create_task(self._push(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _push(self, event: DetectionEvent) -> None:
        sender = self._push_sender
        if sender is None:
            return
        try:
            conn = self._connection()
            viewer_ids = await SQLitePermissionRepository(conn).list_viewer_ids(
                event.camera_name
            )
            subscriptions = SQLitePushSubscriptionRepository(conn)
            targets = await subscriptions.list_for_users(viewer_ids)
            payload = json.dumps(event.to_payload()).encode()
            results = await asyncio.gather(
                *(
                    sender.send(sub.endpoint, sub.p256dh, sub.auth, payload)
                    for sub in targets
                )
            )
            for sub, result in zip(targets, results, strict=True):
                if result is PushResult.GONE:
                    await subscriptions.delete_by_endpoint(sub.endpoint)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Web Push delivery failed for camera %r", event.camera_name
            )

    async def aclose(self) -> None:
        """Let in-flight pushes finish, then release the HTTP client."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._push_sender is not None:
            await self._push_sender.aclose()
