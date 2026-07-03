"""Setup and configuration endpoints: CRUD for cameras and WS-Discovery."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app import state
from app.models.config import (
    CameraConfig,
    CameraConfigRequest,
    CameraConfigResponse,
    DiscoveredCamera,
)
from app.services.camera import RTSPCamera
from app.services.config_writer import add_camera, load_config, remove_camera, update_camera
from app.services.discovery import discover_onvif_cameras
from app.services.ptz import PTZConfig, PTZService
from app.settings import get_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


def _to_response(cam: CameraConfig) -> CameraConfigResponse:
    return CameraConfigResponse(
        name=cam.name,
        host=cam.host,
        rtsp_port=cam.rtsp_port,
        onvif_port=cam.onvif_port,
        user=cam.user,
        password="",
        channel=cam.channel,
        stream=cam.stream,
    )


async def _stop_camera(name: str) -> None:
    cam = state.cameras.get(name)
    if cam is not None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, cam.stop)
        del state.cameras[name]


async def _stop_ptz(name: str) -> None:
    ptz = state.ptz_services.get(name)
    if ptz is not None and ptz.is_connected():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ptz.disconnect)
    if name in state.ptz_services:
        del state.ptz_services[name]


def _start_camera_and_ptz(cam_config: CameraConfig) -> None:
    buf = state.frame_buffers.get(cam_config.name)
    if buf is None:
        from app.buffers.double_frame_buffer import DoubleFrameBuffer
        buf = DoubleFrameBuffer()
        state.frame_buffers[cam_config.name] = buf

    camera = RTSPCamera(rtsp_url=cam_config.rtsp_url, buffer=buf)
    camera.start()
    state.cameras[cam_config.name] = camera

    state.ptz_services[cam_config.name] = PTZService(
        PTZConfig(
            host=cam_config.host,
            port=cam_config.onvif_port,
            user=cam_config.user,
            password=cam_config.password,
        )
    )


@router.get("/api/cameras", response_model=list[CameraConfigResponse])
async def list_cameras() -> list[CameraConfigResponse]:
    """Return all configured cameras. Passwords are always redacted."""
    config = get_config()
    return [_to_response(cam) for cam in config.cameras]


@router.get("/api/cameras/{name}", response_model=CameraConfigResponse)
async def get_camera(name: str) -> CameraConfigResponse:
    """Return a single camera by name. Password is always redacted."""
    config = get_config()
    for cam in config.cameras:
        if cam.name == name:
            return _to_response(cam)
    raise HTTPException(status_code=404, detail=f"Camera '{name}' not found")


@router.post("/api/cameras", response_model=CameraConfigResponse, status_code=201)
async def create_camera(body: CameraConfigRequest) -> CameraConfigResponse:
    """Add a new camera to the config and start its services."""
    cam_config = CameraConfig(**body.model_dump())
    try:
        add_camera(cam_config)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_config.cache_clear()
    _start_camera_and_ptz(cam_config)
    return _to_response(cam_config)


@router.put("/api/cameras/{name}", response_model=CameraConfigResponse)
async def edit_camera(name: str, body: CameraConfigRequest) -> CameraConfigResponse:
    """Update an existing camera and restart its services."""
    if not body.password:
        config = get_config()
        existing = next((c for c in config.cameras if c.name == name), None)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Camera '{name}' not found")
        body = body.model_copy(update={"password": existing.password})

    cam_config = CameraConfig(**body.model_dump())
    try:
        update_camera(name, cam_config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    get_config.cache_clear()
    await _stop_camera(name)
    await _stop_ptz(name)
    if name != cam_config.name:
        old_buf = state.frame_buffers.pop(name, None)
        if old_buf is not None:
            old_buf.shutdown()
    _start_camera_and_ptz(cam_config)
    return _to_response(cam_config)


@router.delete("/api/cameras/{name}")
async def delete_camera(name: str) -> dict[str, str]:
    """Remove a camera from the config and stop its services."""
    try:
        remove_camera(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    get_config.cache_clear()
    await _stop_camera(name)
    await _stop_ptz(name)
    buf = state.frame_buffers.get(name)
    if buf is not None:
        buf.shutdown()
        del state.frame_buffers[name]
    return {"status": "ok"}


@router.get("/api/setup/discover", response_model=list[DiscoveredCamera])
async def discover() -> list[DiscoveredCamera]:
    """Probe the LAN for ONVIF cameras via WS-Discovery."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, discover_onvif_cameras)
