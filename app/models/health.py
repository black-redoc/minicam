"""Response models for the health endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Public summary: counts only, never camera names or config details."""

    status: Literal["ok", "degraded"]
    cameras: int
    cameras_connected: int


class CameraHealth(BaseModel):
    """Per-camera runtime status, for administrators."""

    name: str
    connected: bool
    # Seconds since the capture thread last delivered a frame; None if it
    # never has. Catches a stream that is "connected" but stalled.
    last_frame_age_seconds: float | None
    detection_active: bool
