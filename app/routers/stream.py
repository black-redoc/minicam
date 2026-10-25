from __future__ import annotations

import asyncio
import functools
import logging
import time
import weakref
from collections.abc import Coroutine
from typing import Any, Literal

import numpy as np
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from app import state
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import (
    enforce_wan_access_gate,
    get_authorization_service,
    get_stream_token_service,
    require_can_view,
)
from app.models.auth import StreamTokenResponse
from app.models.domain import User
from app.models.frame import Frame
from app.security_settings import SecuritySettings, get_security_settings
from app.services.authorization import AuthorizationService
from app.services.detection.base import Detection
from app.services.detection.draw import color_for_class, draw_detections
from app.services.encoder import JPEG_QUALITY, WAN_JPEG_QUALITY, encode_jpeg
from app.services.live.feed import LiveFeed
from app.services.live.fmp4 import InitSegment
from app.services.network import NetworkZone, classify_network
from app.services.shared_encoder import SharedJpegEncoder
from app.services.stream_tokens import InvalidStreamTokenError, StreamTokenService

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)

# How often an fMP4 viewer's detection boxes are refreshed when no video
# arrives in between (they are checked after every fragment as well).
_DETECTION_POLL_SECONDS = 0.25
# After a failed write, how long to wait for the disconnect it may stem from.
_DISCONNECT_GRACE_SECONDS = 1.0
# MJPEG frame rate cap for viewers outside the LAN: every JPEG is a whole
# image, so frames beyond this cost uplink bandwidth for little smoothness.
_WAN_MJPEG_MAX_FPS = 10

# One encoder per camera buffer and JPEG quality, dropped automatically with
# the buffer when the camera is removed or re-created by the setup router.
_encoders: weakref.WeakKeyDictionary[FrameBuffer, dict[int, SharedJpegEncoder]] = (
    weakref.WeakKeyDictionary()
)


def _encoder_for(buf: FrameBuffer, quality: int) -> SharedJpegEncoder:
    by_quality = _encoders.get(buf)
    if by_quality is None:
        by_quality = _encoders[buf] = {}
    encoder = by_quality.get(quality)
    if encoder is None:
        encoder = by_quality[quality] = SharedJpegEncoder()
    return encoder


def _draw_and_encode(
    image: np.ndarray, detections: list[Detection], quality: int = JPEG_QUALITY
) -> bytes:
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
    return encode_jpeg(image, quality=quality)


def _render_frame(camera_name: str, quality: int, frame: Frame) -> bytes:
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
    return _draw_and_encode(frame.image, detections, quality)


@router.post("/api/stream/{camera_name}/token", response_model=StreamTokenResponse)
async def issue_stream_token(
    camera_name: str,
    audio: bool = False,
    user: User = Depends(require_can_view),
    _wan_gate: None = Depends(enforce_wan_access_gate),
    stream_token_service: StreamTokenService = Depends(get_stream_token_service),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    settings: SecuritySettings = Depends(get_security_settings),
) -> StreamTokenResponse:
    """Issue a short-lived token authorizing one WebSocket handshake for
    this camera. Requires can_view and, on WAN, an active WAN access gate.

    audio=true also authorizes the camera's audio, and requires can_listen.
    """
    can_listen = await authorization_service.can_listen(user.id, camera_name)
    if audio and not can_listen:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to listen")
    token = stream_token_service.issue(
        user.id, camera_name, settings.stream_token_ttl_seconds, audio=audio
    )
    feed = state.live_feeds.get(camera_name)
    return StreamTokenResponse(
        token=token,
        expires_in=settings.stream_token_ttl_seconds,
        video_mime=feed.mime if feed is not None else None,
        audio_mime=feed.audio_mime if feed is not None and can_listen else None,
    )


@router.websocket("/ws/stream/{camera_name}")
async def stream(
    websocket: WebSocket,
    camera_name: str,
    token: str = Query(...),
    stream_token_service: StreamTokenService = Depends(get_stream_token_service),
    format: Literal["mjpeg", "fmp4"] = "mjpeg",
    audio: bool = False,
    settings: SecuritySettings = Depends(get_security_settings),
) -> None:
    """Stream the camera to the browser over WebSocket.

    format=fmp4 sends the camera's own compressed video as fragmented MP4
    (see _stream_fmp4), with the camera's audio too if audio=true and the
    token was issued for audio. The default, format=mjpeg, waits on the FrameBuffer
    registered for camera_name and sends each new frame as binary JPEG;
    frames this viewer was too slow for are skipped. Outside the LAN, JPEGs
    are encoded at a lower quality and capped at _WAN_MJPEG_MAX_FPS.
    Every frame is encoded once per camera and shared by all its viewers
    (SharedJpegEncoder), with the camera's detection boxes drawn on top
    when there is an unexpired snapshot — that snapshot may lag slightly
    behind the frame, since the detection worker runs at a lower cadence
    than capture/encode; this is an accepted approximation. Closes with code
    1008 if the stream token is missing/invalid/expired/for a different
    camera, or if camera_name is not registered.
    """
    try:
        payload = stream_token_service.verify(token, camera_name)
    except InvalidStreamTokenError:
        logger.warning("Stream rejected — invalid stream token: camera=%s", camera_name)
        await websocket.close(code=1008)
        return
    if audio and (not payload.audio or format != "fmp4"):
        logger.warning("Stream rejected — audio not authorized: camera=%s", camera_name)
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
        "Stream client connected: camera=%s client=%s format=%s audio=%s",
        camera_name,
        websocket.client,
        format,
        audio,
    )
    try:
        if feed is not None and format == "fmp4":
            sender = _stream_fmp4(websocket, camera_name, feed, audio)
        else:
            wan = (
                classify_network(websocket, settings.trusted_proxies) is NetworkZone.WAN
            )
            sender = _stream_mjpeg(websocket, camera_name, buf, wan)
        await _run_until_disconnect(websocket, sender)
    except WebSocketDisconnect as exc:
        logger.info(
            "Stream client disconnected: camera=%s code=%s", camera_name, exc.code
        )
    except Exception:
        logger.exception("Stream error: camera=%s", camera_name)
        raise


async def _run_until_disconnect(
    websocket: WebSocket, sender: Coroutine[Any, Any, None]
) -> None:
    """Run sender until it ends or the client disconnects, whichever is first.

    The senders never read from the socket, so on their own they only notice
    a gone client when a write fails, and never while they wait for a frame
    (stalled camera): reading here prunes those orphans at once. Raises
    WebSocketDisconnect when the client left, also when sender failed first
    writing to the closed connection (uvloop raises RuntimeError there).
    """
    send_task = asyncio.create_task(sender)
    disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))
    tasks = (send_task, disconnect_task)
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if not disconnect_task.done() and send_task.exception() is not None:
            await asyncio.wait({disconnect_task}, timeout=_DISCONNECT_GRACE_SECONDS)
    finally:
        for task in tasks:
            task.cancel()
        # Not gather: anyio's cancel scope (Starlette's TestClient) keeps
        # cancelling a task while it waits on a cancelled gather, and the
        # extra cancellations leak out of the scope.
        await asyncio.wait(tasks)
    if not disconnect_task.cancelled():
        if not send_task.cancelled():
            send_task.exception()  # retrieved: the client leaving explains it
        raise WebSocketDisconnect(code=disconnect_task.result())
    send_task.result()


async def _wait_for_disconnect(websocket: WebSocket) -> int:
    """Read (and ignore) client messages until it disconnects; its close code."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return int(message.get("code", 1000))


async def _stream_mjpeg(
    websocket: WebSocket, camera_name: str, buf: FrameBuffer, wan: bool = False
) -> None:
    quality = WAN_JPEG_QUALITY if wan else JPEG_QUALITY
    min_interval = 1 / _WAN_MJPEG_MAX_FPS if wan else 0.0
    encoder = _encoder_for(buf, quality)
    render = functools.partial(_render_frame, camera_name, quality)
    last_frame: Frame | None = None
    while True:
        frame = await buf.next_frame(last_frame)
        started_at = time.monotonic()
        jpeg = await encoder.encode(frame, render)
        await websocket.send_bytes(jpeg)
        last_frame = frame
        if min_interval:
            # Frames arriving meanwhile are skipped: next_frame returns the latest.
            await asyncio.sleep(max(0.0, started_at + min_interval - time.monotonic()))


async def _stream_fmp4(
    websocket: WebSocket, camera_name: str, feed: LiveFeed, audio: bool = False
) -> None:
    """Send the camera's own video (and audio, if asked) as fragmented MP4,
    plus its detections. Without audio=True no audio is ever sent.

    Messages, in order: a JSON text {"type": "init", "mime": ...} followed
    by the binary init segment (again whenever the camera reconnects), then
    one binary fragment per frame. Detection boxes can't be drawn into
    compressed video, so they arrive as JSON text messages
    {"type": "detections", ...} for the client to draw. Ends (code 1001)
    once the camera is removed or renamed.
    """
    sent_detections: dict[str, Any] | None = None
    with feed.subscribe(audio=audio) as subscription:
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
