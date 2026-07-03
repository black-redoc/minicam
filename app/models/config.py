"""Pydantic models for application and camera configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field


class CameraConfig(BaseModel):
    name: str
    host: str
    rtsp_port: int = 554
    onvif_port: int = 80
    user: str = "admin"
    password: str = ""
    channel: int = 1
    stream: Literal["main", "sub"] = "main"

    @computed_field
    @property
    def rtsp_url(self) -> str:
        return (
            f"rtsp://{self.user}:{self.password}"
            f"@{self.host}:{self.rtsp_port}"
            f"/h264/ch{self.channel}/{self.stream}/av_stream"
        )


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    cameras: list[CameraConfig] = Field(default_factory=list)


class CameraConfigResponse(BaseModel):
    """Read-only view — password always redacted."""

    name: str
    host: str
    rtsp_port: int
    onvif_port: int
    user: str
    password: str
    channel: int
    stream: Literal["main", "sub"]


class CameraConfigRequest(BaseModel):
    """Writable config. Empty password = keep existing."""

    name: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    rtsp_port: int = Field(554, ge=1, le=65535)
    onvif_port: int = Field(80, ge=1, le=65535)
    user: str = "admin"
    password: str = ""
    channel: int = Field(1, ge=1)
    stream: Literal["main", "sub"] = "main"


class DiscoveredCamera(BaseModel):
    """A camera found via WS-Discovery."""

    host: str
    name: str
    hardware: str
    location: str
