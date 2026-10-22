"""Detection notifications: settings, live SSE feed and Web Push opt-in."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.dependencies import (
    enforce_wan_access_gate,
    get_authorization_service,
    get_current_user,
    get_push_subscription_repository,
    require_admin,
)
from app.models.config import (
    NotificationConfig,
    PushSubscriptionRequest,
    PushUnsubscribeRequest,
    VapidPublicKeyResponse,
)
from app.models.domain import User
from app.repositories.push_subscription_repository import PushSubscriptionRepository
from app.services.authorization import AuthorizationService
from app.services.config_writer import update_notification_config
from app.services.notifications import runtime as notification_runtime
from app.services.notifications.events import EventHub
from app.services.notifications.web_push import (
    InvalidSubscriptionError,
    WebPushSender,
    validate_endpoint,
    validate_keys,
)
from app.settings import get_config

router = APIRouter(tags=["notifications"])

_SERVICE_WORKER_PATH = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "service-worker.js"
)
_HEARTBEAT_SECONDS = 15.0
# The stream is closed periodically so EventSource reconnects and the
# session (and WAN gate) are re-checked — a revoked session or a removed
# permission never keeps receiving events on an old connection for long.
_MAX_STREAM_SECONDS = 600.0
_RECONNECT_MILLISECONDS = 3000


def get_event_hub() -> EventHub:
    return notification_runtime.hub


def get_push_sender() -> WebPushSender | None:
    return notification_runtime.service.push_sender


@router.get(
    "/api/notifications/settings",
    response_model=NotificationConfig,
    dependencies=[Depends(require_admin)],
)
async def get_notification_settings() -> NotificationConfig:
    """Return the global (all cameras) notification settings."""
    return get_config().notifications


@router.put(
    "/api/notifications/settings",
    response_model=NotificationConfig,
    dependencies=[Depends(require_admin)],
)
async def update_notification_settings(
    body: NotificationConfig,
) -> NotificationConfig:
    """Persist the global notification settings; applies immediately."""
    config = update_notification_config(body)
    get_config.cache_clear()
    return config.notifications


async def detection_event_stream(
    user_id: int,
    hub: EventHub,
    authorization_service: AuthorizationService,
    heartbeat_seconds: float = _HEARTBEAT_SECONDS,
    max_stream_seconds: float = _MAX_STREAM_SECONDS,
) -> AsyncIterator[str]:
    """Yield SSE frames for the events this user may see, until max age."""
    deadline = time.monotonic() + max_stream_seconds
    with hub.subscribe() as queue:
        yield f"retry: {_RECONNECT_MILLISECONDS}\n\n"
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                event = await asyncio.wait_for(
                    queue.get(), min(heartbeat_seconds, remaining)
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if await authorization_service.can_view(user_id, event.camera_name):
                yield f"event: detection\ndata: {json.dumps(event.to_payload())}\n\n"


@router.get(
    "/api/notifications/events",
    dependencies=[Depends(enforce_wan_access_gate)],
)
async def detection_events(
    user: User = Depends(get_current_user),
    hub: EventHub = Depends(get_event_hub),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> StreamingResponse:
    """Server-Sent Events feed of detections on the cameras the user can view."""
    return StreamingResponse(
        detection_event_stream(user.id, hub, authorization_service),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _require_push_sender(sender: WebPushSender | None) -> WebPushSender:
    if sender is None:
        raise HTTPException(503, "Web Push is not available")
    return sender


@router.get(
    "/api/notifications/push/public-key",
    response_model=VapidPublicKeyResponse,
    dependencies=[Depends(get_current_user)],
)
async def push_public_key(
    sender: WebPushSender | None = Depends(get_push_sender),
) -> VapidPublicKeyResponse:
    """Return the VAPID applicationServerKey browsers subscribe with."""
    return VapidPublicKeyResponse(public_key=_require_push_sender(sender).public_key)


@router.post(
    "/api/notifications/push/subscriptions",
    status_code=201,
    dependencies=[Depends(enforce_wan_access_gate)],
)
async def subscribe_push(
    body: PushSubscriptionRequest,
    user: User = Depends(get_current_user),
    sender: WebPushSender | None = Depends(get_push_sender),
    repository: PushSubscriptionRepository = Depends(get_push_subscription_repository),
) -> dict[str, str]:
    """Register this browser to receive detection notifications via Web Push."""
    _require_push_sender(sender)
    try:
        validate_endpoint(body.endpoint)
        validate_keys(body.keys.p256dh, body.keys.auth)
    except InvalidSubscriptionError as exc:
        raise HTTPException(400, str(exc)) from exc
    await repository.upsert(user.id, body.endpoint, body.keys.p256dh, body.keys.auth)
    return {"status": "ok"}


@router.delete("/api/notifications/push/subscriptions", status_code=204)
async def unsubscribe_push(
    body: PushUnsubscribeRequest,
    user: User = Depends(get_current_user),
    repository: PushSubscriptionRepository = Depends(get_push_subscription_repository),
) -> Response:
    """Stop Web Push for this browser. Idempotent."""
    await repository.delete(user.id, body.endpoint)
    return Response(status_code=204)


@router.get("/service-worker.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    """Serve the service worker from the root so its scope covers every page."""
    return FileResponse(
        _SERVICE_WORKER_PATH,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )
