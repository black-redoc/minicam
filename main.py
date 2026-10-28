import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import state
from app.buffers.frame_buffer import FrameBuffer
from app.db.connection import close_db, init_db
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.repositories.entitlement_sync_repository import (
    SQLiteEntitlementSyncRepository,
)
from app.repositories.usage_repository import SQLiteUsageRepository
from app.routers import auth as auth_router
from app.routers import dashboard
from app.routers import docs as docs_router
from app.routers import health as health_router
from app.routers import mobile as mobile_router
from app.routers import notifications as notifications_router
from app.routers import ptz as ptz_router
from app.routers import recordings as recordings_router
from app.routers import setup as setup_router
from app.routers import stream as stream_router
from app.security_settings import get_security_settings
from app.services.camera import RTSPCamera
from app.services.detection.factory import get_detection_backend
from app.services.detection.worker import DetectionWorker
from app.services.entitlements import (
    EntitlementClient,
    EntitlementSyncService,
    run_sync_loop,
)
from app.services.live.feed import LiveFeed
from app.services.notifications import runtime as notification_runtime
from app.services.ptz import PTZConfig, PTZService
from app.services.recording import runtime as recording_runtime
from app.services.usage import UsageClient, UsageMeter, UsageService, run_usage_loop
from app.settings import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-9s %(asctime)s.%(msecs)03d %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,  # uvicorn's own dictConfig runs before this and would win otherwise
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = get_config()
    security_settings = get_security_settings()
    conn = await init_db(security_settings.db_path)
    await notification_runtime.start_web_push(conn)
    await recording_runtime.service.start()

    central_http: httpx.AsyncClient | None = None
    entitlement_task: asyncio.Task[None] | None = None
    usage_task: asyncio.Task[None] | None = None
    central_url = security_settings.central_url
    device_token = security_settings.device_token
    if central_url and device_token:
        central_http = httpx.AsyncClient()
        sync_service = EntitlementSyncService(
            EntitlementClient(central_http, central_url, device_token),
            SQLiteEntitlementSyncRepository(conn),
        )
        entitlement_task = asyncio.create_task(run_sync_loop(sync_service))
        state.usage_meter = UsageMeter()
        usage_service = UsageService(
            state.usage_meter,
            SQLiteUsageRepository(conn),
            UsageClient(central_http, central_url, device_token),
        )
        usage_task = asyncio.create_task(run_usage_loop(usage_service))
        logger.info("Central mode: syncing WAN entitlements and reporting WAN usage")

    for cam in config.cameras:
        buf = FrameBuffer()
        state.frame_buffers[cam.name] = buf
        live_feed = state.live_feeds[cam.name] = LiveFeed()

        camera = RTSPCamera(
            rtsp_url=cam.rtsp_url,
            buffer=buf,
            name=cam.name,
            recorder=recording_runtime.service.recorder_for(cam.name),
            live_feed=live_feed,
        )
        camera.start()
        state.cameras[cam.name] = camera

        state.ptz_services[cam.name] = PTZService(
            PTZConfig(
                host=cam.host,
                port=cam.onvif_port,
                user=cam.user,
                password=cam.password,
            )
        )
        logger.info("RTSP camera started — name: %s host: %s", cam.name, cam.host)

        if cam.detection_classes:
            backend = get_detection_backend()
            if backend is None:
                logger.warning(
                    "Camera %r requests detection classes %s but no detection "
                    "backend is active — detection disabled for this camera",
                    cam.name,
                    cam.detection_classes,
                )
            else:
                worker = DetectionWorker(
                    camera_name=cam.name,
                    buffer=buf,
                    backend=backend,
                    allowed_classes=frozenset(cam.detection_classes),
                    interval_seconds=1 / config.detection.inference_fps,
                    on_result=notification_runtime.detection_result_handler(cam.name),
                )
                state.detection_workers[cam.name] = asyncio.create_task(worker.run())

    yield

    # Cancelling the usage loop flushes the last counted bytes, so it runs
    # before the database closes.
    central_tasks = [t for t in (entitlement_task, usage_task) if t is not None]
    for task in central_tasks:
        task.cancel()
    await asyncio.gather(*central_tasks, return_exceptions=True)
    state.usage_meter = None
    if central_http is not None:
        await central_http.aclose()
    for task in state.detection_workers.values():
        task.cancel()
    await asyncio.gather(*state.detection_workers.values(), return_exceptions=True)
    for _name, camera in state.cameras.items():
        camera.stop()
    for buf in state.frame_buffers.values():
        buf.shutdown()
    for _name, ptz in state.ptz_services.items():
        if ptz.is_connected():
            ptz.disconnect()
    await recording_runtime.service.shutdown()
    await notification_runtime.shutdown()
    await close_db()
    logger.info("Services shut down")


app = FastAPI(
    title="minicam",
    description="IP Camera Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(stream_router.router)
app.include_router(ptz_router.router)
app.include_router(setup_router.router)
app.include_router(docs_router.router)
app.include_router(health_router.router)
app.include_router(mobile_router.router)
app.include_router(notifications_router.router)
app.include_router(recordings_router.router)
