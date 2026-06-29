"""Pydantic models for the config and setup API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConfigResponse(BaseModel):
    """Read-only view of camera configuration. Password is always redacted."""

    camera_host: str
    camera_rtsp_port: int
    camera_user: str
    camera_password: str  # always "" in responses — never expose the real value
    camera_channel: int
    camera_stream: Literal["main", "sub"]
    camera_onvif_port: int


class ConfigRequest(BaseModel):
    """Writable camera configuration. Empty password means "keep existing"."""

    camera_host: str = Field(..., min_length=1)
    camera_rtsp_port: int = Field(..., ge=1, le=65535)
    camera_user: str
    camera_password: str  # empty string → do not overwrite stored password
    camera_channel: int
    camera_stream: Literal["main", "sub"]
    camera_onvif_port: int = Field(..., ge=1, le=65535)


class DiscoveredCamera(BaseModel):
    """A camera found via WS-Discovery."""

    host: str
    name: str
    hardware: str
    location: str
