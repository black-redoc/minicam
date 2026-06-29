from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app import state

router = APIRouter(prefix="/api/ptz", tags=["ptz"])

_ALLOWED_ACTIONS: frozenset[str] = frozenset({"up", "down", "left", "right", "stop"})


@router.post("/{action}")
async def ptz_command(action: str) -> dict[str, str]:
    """Send a PTZ command to the camera.

    Lazily connects to ONVIF on the first call.
    Blocking ONVIF calls are off-loaded to a thread-pool executor.

    Args:
        action: One of up | down | left | right | stop.
    """
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown PTZ action: '{action}'")

    svc = state.ptz
    if svc is None:
        raise HTTPException(status_code=503, detail="PTZ service not initialized")

    loop = asyncio.get_running_loop()

    try:
        if not svc.is_connected():
            await loop.run_in_executor(None, svc.connect)
        await loop.run_in_executor(None, getattr(svc, action))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"action": action, "status": "ok"}
