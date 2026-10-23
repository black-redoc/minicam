from __future__ import annotations

import asyncio
import functools
import logging
import time
import weakref
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app import state
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import (
    enforce_wan_access_gate,
    get_stream_token_service,
    require_can_view,
)
from app.models.auth import StreamTokenResponse
from app.models.domain import User
from app.models.frame import Frame
from app.security_settings import SecuritySettings, get_security_settings
from app.services.detection.base import Detection
from app.services.detection.draw import color_for_class, draw_detections
from app.services.encoder import encode_jpeg
from app.services.live.feed import LiveFeed
from app.services.live.fmp4 import InitSegment
from app.services.shared_encoder import SharedJpegEncoder
from app.services.stream_tokens import InvalidStreamTokenError, StreamTokenService

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)

# How often an fMP4 viewer's detection boxes are refreshed when no video
# arrives in between (they are checked after every fragment as well).
_DETECTION_POLL_SECONDS = 0.25

# One encoder per camera buffer, dropped automatically with the buffer when
# the camera is removed or re-created by the setup router.
_encoders: weakref.WeakKeyDictionary[FrameBuffer, SharedJpegEncoder] = (
    weakref.WeakKeyDictionary()
)


def _encoder_for(buf: FrameBuffer) -> SharedJpegEncoder:
    encoder = _encoders.get(buf)
    if encoder is None:
        encoder = _encoders[buf] = SharedJpegEncoder()
    return encoder


def _draw_and_encode(image: np.ndarray, detections: list[Detection]) -> bytes:
    """Draw detection boxes (if any) and JPEG-encode, off the event loop.

    Drawing and encoding are combined into one function so the two
    CPU-bound steps share a single run_in_executor() round trip instead of
    two — bouncing back to the event loop between them would only add
    scheduling overhead, since nothing async happens in between.

    When detections is empty, draw_detections is skipped entirely (not
    just a no-op call) so the frame copy it would otherwise make is never
    paid for on the hot path — this keeps the "no active detection for
    this camera" case identical in cost to the pre-detection code path.
    """
    if detections:
        image = draw_detections(image, detections)
    return encode_jpeg(image)


def _render_frame(camera_name: str, frame: Frame) -> bytes:
    """Overlay the camera's current, unexpired detections and encode.

    Runs once per frame in the shared encoder's executor thread, so the
    detection snapshot is looked up here rather than per viewer.
    """
    snapshot = state.detection_results.get(camera_name)
    detections = (
        snapshot.detections
        if snapshot is not None and not snapshot.is_expired(time.monotonic())
        else []
    )
    return _draw_and_encode(frame.image, detections)


@router.post("/api/stream/{camera_name}/token", response_model=StreamTokenResponse)
async def issue_stream_token(
    camera_name: str,
    user: User = Depends(require_can_view),
    _wan_gate: None = Depends(enforce_wan_access_gate),
    stream_token_service: StreamTokenService = Depends(get_stream_token_service),
    settings: SecuritySettings = Depends(get_security_settings),
) -> StreamTokenResponse:
    """Issue a short-lived token authorizing one WebSocket handshake for
    this camera. Requires can_view and, on WAN, an active WAN access gate.
    """
    token = stream_token_service.issue(
        user.id, camera_name, settings.stream_token_ttl_seconds
    )
    feed = state.live_feeds.get(camera_name)
    return StreamTokenResponse(
        token=token,
        expires_in=settings.stream_token_ttl_seconds,
        video_mime=feed.mime if feed is not None else None,
    )


@router.websocket("/ws/stream/{camera_name}")
async def stream(
    websocket: WebSocket,
    camera_name: str,
    token: str = Query(...),
    stream_token_service: StreamTokenService = Depends(get_stream_token_service),
    format: Literal["mjpeg", "fmp4"] = Query("mjpeg"),
) -> None:
    """Stream the camera to the browser over WebSocket.

    format=fmp4 sends the camera's own compressed video as fragmented MP4
    (see _stream_fmp4). The default, format=mjpeg, waits on the FrameBuffer
    registered for camera_name and sends each new frame as binary JPEG;
    frames this viewer was too slow for are skipped.
    Every frame is encoded once per camera and shared by all its viewers
    (SharedJpegEncoder), with the camera's detection boxes drawn on top
    when there is an unexpired snapshot — that snapshot may lag slightly
    behind the frame, since the detection worker runs at a lower cadence
    than capture/encode; this is an accepted approximation. Closes with code
    1008 if the stream token is missing/invalid/expired/for a different
    camera, or if camera_name is not registered.
    """
    try:
        stream_token_service.verify(token, camera_name)
    except InvalidStreamTokenError:
        logger.warning("Stream rejected — invalid stream token: camera=%s", camera_name)
        await websocket.close(code=1008)
        return

    buf = state.frame_buffers.get(camera_name)
    if buf is None:
        logger.warning("Stream rejected — unknown camera: %s", camera_name)
        await websocket.close(code=1008)
        return

    feed = state.live_feeds.get(camera_name)
    if format == "fmp4" and feed is None:
        logger.warning("Stream rejected — no live feed: %s", camera_name)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info(
        "Stream client connected: camera=%s client=%s format=%s",
        camera_name,
        websocket.client,
        format,
    )
    try:
        if feed is not None and format == "fmp4":
            await _stream_fmp4(websocket, camera_name, feed)
        else:
            await _stream_mjpeg(websocket, camera_name, buf)
    except WebSocketDisconnect as exc:
        logger.info(
            "Stream client disconnected: camera=%s code=%s", camera_name, exc.code
        )
    except Exception:
        logger.exception("Stream error: camera=%s", camera_name)
        raise


async def _stream_mjpeg(
    websocket: WebSocket, camera_name: str, buf: FrameBuffer
) -> None:
    encoder = _encoder_for(buf)
    render = functools.partial(_render_frame, camera_name)
    last_frame: Frame | None = None
    while True:
        frame = await buf.next_frame(last_frame)
        jpeg = await encoder.encode(frame, render)
        await websocket.send_bytes(jpeg)
        last_frame = frame


async def _stream_fmp4(websocket: WebSocket, camera_name: str, feed: LiveFeed) -> None:
    """Send the camera's own video as fragmented MP4, plus its detections.

    Messages, in order: a JSON text {"type": "init", "mime": ...} followed
    by the binary init segment (again whenever the camera reconnects), then
    one binary fragment per frame. Detection boxes can't be drawn into
    compressed video, so they arrive as JSON text messages
    {"type": "detections", ...} for the client to draw. Ends (code 1001)
    once the camera is removed or renamed.
    """
    sent_detections: dict[str, Any] | None = None
    with feed.subscribe() as subscription:
        while state.live_feeds.get(camera_name) is feed:
            try:
                message = await asyncio.wait_for(
                    subscription.next(), _DETECTION_POLL_SECONDS
                )
            except TimeoutError:
                message = None
            if isinstance(message, InitSegment):
                await websocket.send_json({"type": "init", "mime": message.mime})
            if message is not None:
                await websocket.send_bytes(message.data)
            detections = _detections_message(camera_name)
            if detections != sent_detections:
                await websocket.send_json(detections)
                sent_detections = detections
    await websocket.close(code=1001)


def _detections_message(camera_name: str) -> dict[str, Any]:
    """The camera's current, unexpired detections, in frame pixels."""
    snapshot = state.detection_results.get(camera_name)
    detections = (
        snapshot.detections
        if snapshot is not None and not snapshot.is_expired(time.monotonic())
        else []
    )
    buf = state.frame_buffers.get(camera_name)
    frame = buf.read() if buf is not None else None
    return {
        "type": "detections",
        "width": frame.width if frame is not None else 0,
        "height": frame.height if frame is not None else 0,
        "boxes": [
            {
                "label": d.class_name,
                "confidence": round(d.confidence, 2),
                "color": "#{:02x}{:02x}{:02x}".format(*color_for_class(d.class_name)),
                "x1": d.x1,
                "y1": d.y1,
                "x2": d.x2,
                "y2": d.y2,
            }
            for d in detections
        ],
    }
