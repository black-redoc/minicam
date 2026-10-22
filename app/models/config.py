"""Pydantic models for application and camera configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

# Per-camera notification override: "inherit" follows the global
# [notifications] switch, "on"/"off" force it for this camera only.
NotificationMode = Literal["inherit", "on", "off"]


class CameraConfig(BaseModel):
    name: str
    host: str
    rtsp_port: int = 554
    onvif_port: int = 80
    user: str = "admin"
    password: str = ""
    channel: int = 1
    stream: Literal["main", "sub"] = "main"
    detection_classes: list[str] = Field(default_factory=list)
    notifications: NotificationMode = "inherit"

    # The pydantic.mypy plugin does not special-case @computed_field stacked
    # on @property in this pydantic/mypy version combination, so mypy's core
    # "decorators on @property are unsupported" rule still fires here. This
    # is pydantic's own documented workaround for that known false positive.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def rtsp_url(self) -> str:
        return (
            f"rtsp://{self.user}:{self.password}"
            f"@{self.host}:{self.rtsp_port}"
            f"/h264/ch{self.channel}/{self.stream}/av_stream"
        )


class DetectionConfig(BaseModel):
    """Object detection engine configuration, persisted in config.toml."""

    backend: Literal["none", "onnx"] = "none"
    model_path: str = ""
    confidence_threshold: float = Field(0.5, ge=0.0, le=1.0)
    inference_fps: float = Field(3.0, gt=0.0)


class NotificationConfig(BaseModel):
    """Detection notification settings, persisted in config.toml."""

    enabled: bool = False
    cooldown_seconds: int = Field(60, ge=1, le=86400)


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    cameras: list[CameraConfig] = Field(default_factory=list)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)


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
    detection_classes: list[str] = Field(default_factory=list)
    notifications: NotificationMode = "inherit"


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
    detection_classes: list[str] = Field(default_factory=list)
    notifications: NotificationMode = "inherit"


class DiscoveredCamera(BaseModel):
    """A camera found via WS-Discovery."""

    host: str
    name: str
    hardware: str
    location: str


class DetectionClassesResponse(BaseModel):
    """Class names the active detection backend can recognize."""

    classes: list[str] = Field(default_factory=list)


class ModelFetchRequest(BaseModel):
    """Request body to fetch a .onnx model from a remote URL."""

    url: str


class CatalogAsset(BaseModel):
    """One installable model file from a minicam-models release manifest."""

    name: str
    model: str
    family: str
    precision: str
    profile: str
    bytes: int
    sha256: str
    url: str


class ModelCatalog(BaseModel):
    release: str
    assets: list[CatalogAsset]


class CatalogAssetStatus(CatalogAsset):
    """A catalog asset plus its state on this device."""

    installed: bool
    active: bool


class ModelCatalogResponse(BaseModel):
    catalog_url: str
    release: str
    assets: list[CatalogAssetStatus]


class CatalogInstallRequest(BaseModel):
    """Install a catalog asset by name; the server looks up URL and sha256."""

    name: str
    activate: bool = True


class CatalogInstallResponse(BaseModel):
    model_path: str
    sha256: str
    active: bool


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=256)
    auth: str = Field(..., min_length=1, max_length=64)


class PushSubscriptionRequest(BaseModel):
    """A browser PushSubscription, as returned by PushSubscription.toJSON()."""

    endpoint: str = Field(..., min_length=1, max_length=2048)
    keys: PushSubscriptionKeys


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=2048)


class VapidPublicKeyResponse(BaseModel):
    public_key: str
