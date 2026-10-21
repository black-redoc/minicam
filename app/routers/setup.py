"""Setup and configuration endpoints: CRUD for cameras and WS-Discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import state
from app.dependencies import require_admin
from app.models.config import (
    CameraConfig,
    CameraConfigRequest,
    CameraConfigResponse,
    DetectionClassesResponse,
    DetectionConfig,
    DiscoveredCamera,
    ModelFetchRequest,
)
from app.services.camera import RTSPCamera
from app.services.config_writer import (
    add_camera,
    remove_camera,
    update_camera,
    update_detection_config,
)
from app.services.detection.factory import get_detection_backend
from app.services.detection.worker import DetectionWorker
from app.services.discovery import discover_onvif_cameras
from app.services.ptz import PTZConfig, PTZService
from app.settings import get_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"], dependencies=[Depends(require_admin)])

_ONNX_BACKEND_NAME = "onnx"
_MODEL_UPLOAD_ALLOWED_SUFFIX = ".onnx"
_MAX_MODEL_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
_MODEL_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB
_MAX_MODEL_FETCH_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models"


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
        detection_classes=cam.detection_classes,
    )


def _validate_detection_classes(requested: list[str]) -> None:
    """Reject unknown detection classes when a backend is active.

    With no backend configured there is nothing to validate against, so
    requested classes are accepted as-is and simply take effect once a
    backend is installed later.
    """
    backend = get_detection_backend()
    if backend is None:
        return

    unknown = set(requested) - set(backend.available_classes)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown detection classes: {sorted(unknown)}",
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


async def _stop_detection(name: str) -> None:
    task = state.detection_workers.get(name)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        state.detection_workers.pop(name, None)
    state.detection_results.pop(name, None)


def _start_detection(cam_config: CameraConfig) -> None:
    if not cam_config.detection_classes:
        return

    backend = get_detection_backend()
    if backend is None:
        logger.warning(
            "Camera %r requests detection classes %s but no detection "
            "backend is active — detection disabled for this camera",
            cam_config.name,
            cam_config.detection_classes,
        )
        return

    buf = state.frame_buffers[cam_config.name]
    worker = DetectionWorker(
        camera_name=cam_config.name,
        buffer=buf,
        backend=backend,
        allowed_classes=frozenset(cam_config.detection_classes),
        interval_seconds=1 / get_config().detection.inference_fps,
        on_result=lambda snap: state.detection_results.__setitem__(
            cam_config.name, snap
        ),
    )
    state.detection_workers[cam_config.name] = asyncio.create_task(worker.run())


def _start_camera_and_ptz(cam_config: CameraConfig) -> None:
    buf = state.frame_buffers.get(cam_config.name)
    if buf is None:
        from app.buffers.frame_buffer import FrameBuffer

        buf = FrameBuffer()
        state.frame_buffers[cam_config.name] = buf

    camera = RTSPCamera(rtsp_url=cam_config.rtsp_url, buffer=buf, name=cam_config.name)
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
    _validate_detection_classes(body.detection_classes)
    cam_config = CameraConfig(**body.model_dump())
    try:
        add_camera(cam_config)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_config.cache_clear()
    _start_camera_and_ptz(cam_config)
    _start_detection(cam_config)
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

    _validate_detection_classes(body.detection_classes)
    cam_config = CameraConfig(**body.model_dump())
    try:
        update_camera(name, cam_config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    get_config.cache_clear()
    await _stop_camera(name)
    await _stop_ptz(name)
    await _stop_detection(name)
    if name != cam_config.name:
        old_buf = state.frame_buffers.pop(name, None)
        if old_buf is not None:
            old_buf.shutdown()
    _start_camera_and_ptz(cam_config)
    _start_detection(cam_config)
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
    await _stop_detection(name)
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


@router.get("/api/detection/classes", response_model=DetectionClassesResponse)
async def detection_classes() -> DetectionClassesResponse:
    """List class names the active detection backend can recognize."""
    backend = get_detection_backend()
    if backend is None:
        return DetectionClassesResponse(classes=[])
    return DetectionClassesResponse(classes=list(backend.available_classes))


def _validate_onnx_model_path(model_path: str) -> None:
    """Reject a non-existent or non-absolute model path for the onnx backend."""
    path = Path(model_path)
    if not path.is_absolute() or not path.is_file():
        raise HTTPException(
            status_code=400,
            detail=(f"model_path {model_path!r} is not an existing absolute file"),
        )


async def _restart_active_detection_workers() -> None:
    """Restart detection workers for every camera with detection classes set."""
    config = get_config()
    for cam in config.cameras:
        if not cam.detection_classes:
            continue
        await _stop_detection(cam.name)
        _start_detection(cam)


@router.get("/api/detection/settings", response_model=DetectionConfig)
async def get_detection_configuration() -> DetectionConfig:
    """Return the current detection engine configuration."""
    return get_config().detection


@router.put("/api/detection/settings", response_model=DetectionConfig)
async def update_detection_configuration(body: DetectionConfig) -> DetectionConfig:
    """Update the detection engine configuration and apply it at runtime."""
    if body.backend == _ONNX_BACKEND_NAME:
        _validate_onnx_model_path(body.model_path)

    config = update_detection_config(body)
    get_config.cache_clear()
    get_detection_backend.cache_clear()
    await _restart_active_detection_workers()
    return config.detection


def _safe_model_name(filename: str) -> str:
    """Sanitize a filename and reject anything not ending in .onnx."""
    safe_name = Path(filename).name
    if not safe_name.endswith(_MODEL_UPLOAD_ALLOWED_SUFFIX):
        raise HTTPException(status_code=400, detail="Only .onnx files are accepted")
    return safe_name


async def _write_model_chunks(safe_name: str, chunks: AsyncIterator[bytes]) -> Path:
    """Stream chunks to _MODELS_DIR/safe_name, enforcing the size cap.

    Chunks go to a temporary file in the same directory (never held in RAM,
    which matters on a Raspberry Pi with a 200 MB cap) and are only moved
    into place with an atomic os.replace() once the whole stream passes
    validation, so a size-cap breach or network failure never leaves a
    partial file behind or clobbers an existing model.
    """
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    destination = _MODELS_DIR / safe_name
    fd, tmp_name = tempfile.mkstemp(dir=_MODELS_DIR, suffix=".part")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            written = 0
            async for chunk in chunks:
                written += len(chunk)
                if written > _MAX_MODEL_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=400, detail="Model file is too large"
                    )
                await asyncio.to_thread(tmp_file.write, chunk)
        os.replace(tmp_path, destination)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return destination


def _require_https_url(url: str) -> None:
    """Reject any scheme other than https:// for a remote model URL."""
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Only https:// URLs are accepted")


async def _resolve_host(host: str) -> list[str]:
    """Return every IP address host resolves to (mockable seam for tests)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def _require_public_host(url: str) -> None:
    """Reject URLs whose host is, or resolves to, a non-public address.

    Blocks the fetch endpoint from being pointed at the LAN, loopback or
    link-local services (cameras, router, cloud metadata). A DNS answer
    that changes between this check and the actual connect is not covered;
    the endpoint is admin-only, so this is defense in depth, not the sole
    control.
    """
    host = httpx.URL(url).host
    if not host:
        raise HTTPException(status_code=400, detail="URL has no host")
    try:
        addresses = await _resolve_host(host)
    except OSError as exc:
        raise HTTPException(
            status_code=502, detail=f"Cannot resolve host: {host}"
        ) from exc
    # Strip any IPv6 zone id ("fe80::1%eth0"), which ip_address() rejects.
    if not addresses or not all(
        ipaddress.ip_address(addr.split("%", 1)[0]).is_global for addr in addresses
    ):
        raise HTTPException(status_code=400, detail="URL must point to a public host")


async def _validate_fetch_url(url: str) -> None:
    _require_https_url(url)
    await _require_public_host(url)


async def _stream_model_url(url: str, safe_name: str) -> Path:
    """Download a remote .onnx file to _MODELS_DIR, enforcing the size cap.

    Redirects are followed manually (follow_redirects=False) so every hop is
    re-validated: an https:// URL must not be able to bounce the download to
    plain http:// or to an internal address.
    """
    timeout = httpx.Timeout(10.0, read=60.0)
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            for _ in range(_MAX_MODEL_FETCH_REDIRECTS + 1):
                await _validate_fetch_url(url)
                async with client.stream("GET", url) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(
                                status_code=502,
                                detail="Redirect without a Location header",
                            )
                        url = str(httpx.URL(url).join(location))
                        continue
                    if response.status_code != 200:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Fetch failed with status {response.status_code}",
                        )
                    return await _write_model_chunks(
                        safe_name, response.aiter_bytes(_MODEL_UPLOAD_CHUNK_BYTES)
                    )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc

    raise HTTPException(status_code=502, detail="Too many redirects")


@router.post("/api/detection/model")
async def upload_detection_model(file: UploadFile = File(...)) -> dict[str, str]:
    """Upload a .onnx model file for later activation via PUT settings."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Only .onnx files are accepted")
    safe_name = _safe_model_name(file.filename)

    # Read in bounded chunks instead of a single file.read(): Content-Length
    # is client-supplied and cannot be trusted, so the size cap must be
    # enforced against bytes actually received.
    async def upload_chunks() -> AsyncIterator[bytes]:
        while chunk := await file.read(_MODEL_UPLOAD_CHUNK_BYTES):
            yield chunk

    destination = await _write_model_chunks(safe_name, upload_chunks())
    return {"model_path": str(destination)}


@router.post("/api/detection/model/fetch")
async def fetch_detection_model(body: ModelFetchRequest) -> dict[str, str]:
    """Download a .onnx model from a URL for later activation via PUT settings."""
    _require_https_url(body.url)
    filename = Path(httpx.URL(body.url).path).name
    if not filename:
        raise HTTPException(status_code=400, detail="URL has no filename")
    safe_name = _safe_model_name(filename)

    destination = await _stream_model_url(body.url, safe_name)
    return {"model_path": str(destination)}
