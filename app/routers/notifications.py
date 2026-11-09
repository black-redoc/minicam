"""Detection notifications: settings, live SSE feed, Web Push and mobile push opt-in."""

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
    get_mobile_push_relay,
    get_mobile_push_token_repository,
    get_push_subscription_repository,
    get_session_jti,
    require_admin,
)
from app.models.config import (
    MobilePushStatusResponse,
    MobilePushTokenRequest,
    MobilePushUnregisterRequest,
    NotificationConfig,
    PushSubscriptionRequest,
    PushUnsubscribeRequest,
    VapidPublicKeyResponse,
)
from app.models.domain import User
from app.repositories.mobile_push_token_repository import MobilePushTokenRepository
from app.repositories.push_subscription_repository import PushSubscriptionRepository
from app.services.authorization import AuthorizationService
from app.services.config_writer import update_notification_config
from app.services.notifications import runtime as notification_runtime
from app.services.notifications.events import EventHub
from app.services.notifications.mobile_push import (
    InvalidMobileTokenError,
    MobilePushRelay,
    RelayError,
    validate_key,
    validate_platform,
    validate_token,
)
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


def _require_mobile_relay(relay: MobilePushRelay | None) -> MobilePushRelay:
    if relay is None:
        raise HTTPException(503, "Mobile push needs the central dashboard")
    return relay


@router.get(
    "/api/notifications/mobile/status",
    response_model=MobilePushStatusResponse,
    dependencies=[Depends(get_current_user)],
)
async def mobile_push_status(
    relay: MobilePushRelay | None = Depends(get_mobile_push_relay),
) -> MobilePushStatusResponse:
    """Whether this device can push to the apps: paired and FCM set up at the relay."""
    return MobilePushStatusResponse(
        available=relay is not None and await relay.available()
    )


@router.post(
    "/api/notifications/mobile/tokens",
    status_code=201,
    dependencies=[Depends(enforce_wan_access_gate)],
)
async def register_mobile_token(
    body: MobilePushTokenRequest,
    user: User = Depends(get_current_user),
    session_jti: str = Depends(get_session_jti),
    relay: MobilePushRelay | None = Depends(get_mobile_push_relay),
    repository: MobilePushTokenRepository = Depends(get_mobile_push_token_repository),
) -> dict[str, str]:
    """Register this phone for detection pushes (ADR 0011). The relay learns the
    token first, so a stored token is always one the relay can reach."""
    try:
        validate_platform(body.platform)
        validate_token(body.token)
        validate_key(body.key)
    except InvalidMobileTokenError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        await _require_mobile_relay(relay).register(
            user.email, body.platform, body.token
        )
    except RelayError as exc:
        raise HTTPException(
            502, "The central dashboard did not accept the phone"
        ) from exc
    await repository.upsert(user.id, session_jti, body.platform, body.token, body.key)
    return {"status": "ok"}


@router.delete("/api/notifications/mobile/tokens", status_code=204)
async def unregister_mobile_token(
    body: MobilePushUnregisterRequest,
    user: User = Depends(get_current_user),
    relay: MobilePushRelay | None = Depends(get_mobile_push_relay),
    repository: MobilePushTokenRepository = Depends(get_mobile_push_token_repository),
) -> Response:
    """Stop pushes to this phone. Idempotent; the relay is told best-effort."""
    if await repository.delete(user.id, body.token) and relay is not None:
        await relay.unregister(body.token)
    return Response(status_code=204)


@router.get("/service-worker.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    """Serve the service worker from the root so its scope covers every page."""
    return FileResponse(
        _SERVICE_WORKER_PATH,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )
