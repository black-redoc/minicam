from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app import state

router = APIRouter(prefix="/api/ptz", tags=["ptz"])

_ALLOWED_ACTIONS: frozenset[str] = frozenset({"up", "down", "left", "right", "stop"})
_CONNECTION_ERROR_KEYWORDS: frozenset[str] = frozenset(
    {
        "Connection aborted",
        "Connection reset",
        "ConnectionReset",
        "ConnectionError",
        "RemoteDisconnected",
    }
)


def _is_connection_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(keyword in msg for keyword in _CONNECTION_ERROR_KEYWORDS)


@router.post("/{camera_name}/{action}")
async def ptz_command(camera_name: str, action: str) -> dict[str, str]:
    """Send a PTZ command to the named camera.

    Lazily connects to ONVIF on the first call.
    Blocking ONVIF calls are off-loaded to a thread-pool executor.

    Args:
        camera_name: Key used to look up the PTZ service in the registry.
        action: One of up | down | left | right | stop.
    """
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown PTZ action: '{action}'")

    svc = state.ptz_services.get(camera_name)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail=f"PTZ service not found for camera '{camera_name}'",
        )

    loop = asyncio.get_running_loop()

    try:
        await loop.run_in_executor(state.ptz_executor, svc.run_command, action)
    except Exception as exc:
        if _is_connection_error(exc):
            try:
                await loop.run_in_executor(
                    state.ptz_executor, svc.reconnect_and_run, action
                )
            except Exception as retry_exc:
                raise HTTPException(
                    status_code=503, detail=str(retry_exc)
                ) from retry_exc
        else:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"action": action, "status": "ok"}
