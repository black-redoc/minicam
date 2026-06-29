from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import state
from app.services.encoder import encode_jpeg

router = APIRouter(tags=["stream"])

_POLL_INTERVAL = 1 / 60  # check buffer at 60 Hz, send only on new frames


@router.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    """Stream JPEG frames to the browser over WebSocket.

    Reads from the shared DoubleFrameBuffer and sends each new frame as
    binary JPEG. Encoding runs in a thread-pool executor to keep the
    async event loop responsive.
    """
    await websocket.accept()
    loop = asyncio.get_running_loop()
    last_sequence = -1

    try:
        while True:
            frame = state.frame_buffer.read()

            if frame is not None and frame.sequence != last_sequence:
                jpeg = await loop.run_in_executor(None, encode_jpeg, frame.image)
                await websocket.send_bytes(jpeg)
                last_sequence = frame.sequence

            await asyncio.sleep(_POLL_INTERVAL)

    except WebSocketDisconnect:
        pass
