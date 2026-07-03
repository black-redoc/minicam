from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import state
from app.services.encoder import encode_jpeg

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1 / 60


@router.websocket("/ws/stream/{camera_name}")
async def stream(websocket: WebSocket, camera_name: str) -> None:
    """Stream JPEG frames to the browser over WebSocket.

    Reads from the DoubleFrameBuffer registered for camera_name and sends
    each new frame as binary JPEG. Closes with code 1008 if camera_name is
    not registered. Encoding runs in a thread-pool executor to keep the
    async event loop responsive.
    """
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
