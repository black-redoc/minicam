"""Setup and configuration endpoints: config read/write and camera discovery."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from app import settings as _settings_mod
from app import state
from app.models.config import ConfigRequest, ConfigResponse, DiscoveredCamera
from app.services.camera import RTSPCamera
from app.services.config_writer import read_env_config, write_env_config
from app.services.discovery import discover_onvif_cameras
from app.services.ptz import PTZConfig, PTZService
from app.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])

_ENV_KEY_MAP: dict[str, str] = {
    "camera_host": "CAMERA_HOST",
    "camera_rtsp_port": "CAMERA_RTSP_PORT",
    "camera_user": "CAMERA_USER",
    "camera_password": "CAMERA_PASSWORD",
    "camera_channel": "CAMERA_CHANNEL",
    "camera_stream": "CAMERA_STREAM",
    "camera_onvif_port": "CAMERA_ONVIF_PORT",
}


@router.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Return current camera configuration. Password is always redacted."""
    settings = get_settings()
    return ConfigResponse(
        camera_host=settings.camera_host or "",
        camera_rtsp_port=settings.camera_rtsp_port,
        camera_user=settings.camera_user,
        camera_password="",
        camera_channel=settings.camera_channel,
        camera_stream=settings.camera_stream,  # type: ignore[arg-type]
        camera_onvif_port=settings.camera_onvif_port,
    )


def _build_env_updates(body: ConfigRequest) -> dict[str, str]:
    """Build the .env updates dict from a config request.

    Empty password is excluded so the stored credential is preserved.
    """
    updates: dict[str, str] = {
        _ENV_KEY_MAP["camera_host"]: body.camera_host,
        _ENV_KEY_MAP["camera_rtsp_port"]: str(body.camera_rtsp_port),
        _ENV_KEY_MAP["camera_user"]: body.camera_user,
        _ENV_KEY_MAP["camera_channel"]: str(body.camera_channel),
        _ENV_KEY_MAP["camera_stream"]: body.camera_stream,
        _ENV_KEY_MAP["camera_onvif_port"]: str(body.camera_onvif_port),
    }
    if body.camera_password != "":
        updates[_ENV_KEY_MAP["camera_password"]] = body.camera_password
    return updates


async def _stop_camera() -> None:
    """Stop the running camera in a thread pool to avoid blocking the event loop."""
    if state.camera is not None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, state.camera.stop)


async def _disconnect_ptz() -> None:
    """Disconnect PTZ service in a thread pool if currently connected."""
    if state.ptz is not None and state.ptz.is_connected():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, state.ptz.disconnect)


def _start_camera_and_ptz(settings: object) -> None:
    """Create and start fresh camera and PTZ instances from new settings."""
    from app.settings import Settings  # local import avoids circular reference

    s: Settings = settings  # type: ignore[assignment]
    if s.camera_host is None or s.rtsp_url is None:
        return

    state.camera = RTSPCamera(rtsp_url=s.rtsp_url, buffer=state.frame_buffer)
    state.camera.start()

    state.ptz = PTZService(
        PTZConfig(
            host=s.camera_host,
            port=s.camera_onvif_port,
            user=s.camera_user,
            password=s.camera_password or "",
        )
    )


@router.put("/api/config")
async def update_config(body: ConfigRequest) -> dict[str, str]:
    """Persist new camera config to .env and restart camera/PTZ services.

    An empty password string in the request means "keep existing password".
    """
    updates = _build_env_updates(body)
    write_env_config(updates)

    await _stop_camera()
    await _disconnect_ptz()

    # Call cache_clear on the canonical reference so patches don't break it.
    _settings_mod.get_settings.cache_clear()
    new_settings = get_settings()

    _start_camera_and_ptz(new_settings)

    return {"status": "ok"}


@router.get("/api/setup/discover", response_model=list[DiscoveredCamera])
async def discover() -> list[DiscoveredCamera]:
    """Probe the LAN for ONVIF cameras via WS-Discovery."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, discover_onvif_cameras)
