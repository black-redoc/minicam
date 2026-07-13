from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app import state
from app.dependencies import (
    enforce_wan_access_gate,
    get_stream_token_service,
    require_can_view,
)
from app.models.auth import StreamTokenResponse
from app.models.domain import User
from app.security_settings import SecuritySettings, get_security_settings
from app.services.encoder import encode_jpeg
from app.services.stream_tokens import InvalidStreamTokenError, StreamTokenService

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1 / 60


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
    return StreamTokenResponse(
        token=token, expires_in=settings.stream_token_ttl_seconds
    )


@router.websocket("/ws/stream/{camera_name}")
async def stream(
    websocket: WebSocket,
    camera_name: str,
    token: str = Query(...),
    stream_token_service: StreamTokenService = Depends(get_stream_token_service),
) -> None:
    """Stream JPEG frames to the browser over WebSocket.

    Reads from the DoubleFrameBuffer registered for camera_name and sends
    each new frame as binary JPEG. Closes with code 1008 if the stream
    token is missing/invalid/expired/for a different camera, or if
    camera_name is not registered. Encoding runs in a thread-pool executor
    to keep the async event loop responsive.
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

    await websocket.accept()
    logger.info(
        "Stream client connected: camera=%s client=%s", camera_name, websocket.client
    )
    loop = asyncio.get_running_loop()
    last_sequence = -1

    try:
        while True:
            frame = buf.read()

            if frame is not None and frame.sequence != last_sequence:
                jpeg = await loop.run_in_executor(None, encode_jpeg, frame.image)
                await websocket.send_bytes(jpeg)
                last_sequence = frame.sequence

            await asyncio.sleep(_POLL_INTERVAL)

    except WebSocketDisconnect as exc:
        logger.info(
            "Stream client disconnected: camera=%s code=%s", camera_name, exc.code
        )
    except Exception:
        logger.exception("Stream error: camera=%s", camera_name)
        raise
