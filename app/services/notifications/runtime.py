"""Process-wide notification wiring shared by main.py and the routers.

Like app.state, this holds singletons: the SSE hub, the per-camera
rate limiter and the delivery service. Settings are read from get_config()
on every decision, so toggling notifications in the dashboard takes effect
without restarting any detection worker.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

import aiosqlite

from app import state
from app.db.connection import get_connection
from app.repositories.push_subscription_repository import (
    SQLitePushSubscriptionRepository,
)
from app.services.detection.worker import DetectionSnapshot
from app.services.notifications.events import EventHub
from app.services.notifications.notifier import (
    DetectionNotifier,
    notifications_enabled_for,
)
from app.services.notifications.service import NotificationService
from app.services.notifications.web_push import VapidKey, WebPushSender
from app.settings import get_config

logger = logging.getLogger(__name__)

_DEFAULT_VAPID_SUBJECT = "mailto:minicam@example.com"


def _camera_enabled(camera_name: str) -> bool:
    config = get_config()
    camera = next((c for c in config.cameras if c.name == camera_name), None)
    return camera is not None and notifications_enabled_for(
        camera, config.notifications
    )


def _cooldown_seconds() -> float:
    return float(get_config().notifications.cooldown_seconds)


hub = EventHub()
service = NotificationService(hub, connection=get_connection)
notifier = DetectionNotifier(
    is_enabled=_camera_enabled,
    cooldown_seconds=_cooldown_seconds,
    on_event=service.handle,
)


def detection_result_handler(camera_name: str) -> Callable[[DetectionSnapshot], None]:
    """Build a DetectionWorker on_result callback for one camera."""

    def handle(snapshot: DetectionSnapshot) -> None:
        state.detection_results[camera_name] = snapshot
        notifier.on_snapshot(camera_name, snapshot)

    return handle


async def start_web_push(conn: aiosqlite.Connection) -> None:
    """Load (or create on first start) the VAPID key and enable Web Push."""
    repository = SQLitePushSubscriptionRepository(conn)
    pem = await repository.get_vapid_private_key()
    if pem is None:
        pem = await repository.save_vapid_private_key(VapidKey.generate_pem())
        logger.info("Generated a new VAPID key for Web Push")
    subject = os.environ.get("MINICAM_VAPID_SUBJECT") or _DEFAULT_VAPID_SUBJECT
    service.set_push_sender(WebPushSender(VapidKey.from_pem(pem, subject)))


async def shutdown() -> None:
    await service.aclose()
    service.set_push_sender(None)
