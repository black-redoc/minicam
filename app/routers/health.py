"""Health endpoints: a public summary and an admin-only per-camera view."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from app import state
from app.dependencies import require_admin
from app.models.health import CameraHealth, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report whether every running camera is receiving frames.

    Unauthenticated on purpose (for uptime monitors), so it exposes counts
    only: camera names stay behind authorization, consistent with the
    404-not-403 rule in require_can_view. Always 200 — a camera being down
    is "degraded", not a reason for a supervisor to restart the app.
    """
    total = len(state.cameras)
    connected = sum(1 for camera in state.cameras.values() if camera.is_connected())
    return HealthResponse(
        status="ok" if connected == total else "degraded",
        cameras=total,
        cameras_connected=connected,
    )


@router.get(
    "/api/health/cameras",
    response_model=list[CameraHealth],
    dependencies=[Depends(require_admin)],
)
def camera_health() -> list[CameraHealth]:
    """Per-camera status, sorted by name. Requires an admin session."""
    now = time.monotonic()
    result = []
    for name, camera in sorted(state.cameras.items()):
        buf = state.frame_buffers.get(name)
        worker = state.detection_workers.get(name)
        result.append(
            CameraHealth(
                name=name,
                connected=camera.is_connected(),
                last_frame_age_seconds=(
                    buf.seconds_since_write(now) if buf is not None else None
                ),
                detection_active=worker is not None and not worker.done(),
            )
        )
    return result
