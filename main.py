import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import state
from app.routers import dashboard
from app.routers import ptz as ptz_router
from app.routers import setup as setup_router
from app.routers import stream as stream_router
from app.services.camera import RTSPCamera
from app.services.ptz import PTZConfig, PTZService
from app.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    if settings.camera_host is not None and settings.rtsp_url is not None:
        state.camera = RTSPCamera(
            rtsp_url=settings.rtsp_url,
            buffer=state.frame_buffer,
        )
        state.camera.start()
        logger.info("RTSP camera started — host: %s", settings.camera_host)

        state.ptz = PTZService(
            PTZConfig(
                host=settings.camera_host,
                port=settings.camera_onvif_port,
                user=settings.camera_user,
                password=settings.camera_password or "",
            )
        )

    yield

    if state.camera:
        state.camera.stop()
    state.frame_buffer.shutdown()
    if state.ptz and state.ptz.is_connected():
        state.ptz.disconnect()
    logger.info("Services shut down")


app = FastAPI(
    title="minicam",
    description="IP Camera Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard.router)
app.include_router(stream_router.router)
app.include_router(ptz_router.router)
app.include_router(setup_router.router)


@app.get("/health")
def health() -> dict[str, str | bool]:
    """JSON health status."""
    settings = get_settings()
    return {
        "status": "ok",
        "camera_host": settings.camera_host or "",
        "camera_connected": state.camera.is_connected() if state.camera else False,
        "debug": settings.debug,
    }
