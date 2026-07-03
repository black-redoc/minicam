import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import state
from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.routers import dashboard
from app.routers import ptz as ptz_router
from app.routers import docs as docs_router
from app.routers import setup as setup_router
from app.routers import stream as stream_router
from app.services.camera import RTSPCamera
from app.services.ptz import PTZConfig, PTZService
from app.settings import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-9s %(asctime)s.%(msecs)03d %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,  # uvicorn's own dictConfig runs before this and would win otherwise
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = get_config()

    for cam in config.cameras:
        buf = DoubleFrameBuffer()
        state.frame_buffers[cam.name] = buf

        camera = RTSPCamera(rtsp_url=cam.rtsp_url, buffer=buf)
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

    yield

    for name, camera in state.cameras.items():
        camera.stop()
    for buf in state.frame_buffers.values():
        buf.shutdown()
    for name, ptz in state.ptz_services.items():
        if ptz.is_connected():
            ptz.disconnect()
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
app.include_router(docs_router.router)


@app.get("/health")
def health() -> dict[str, str | int | bool]:
    """JSON health status."""
    config = get_config()
    return {
        "status": "ok",
        "cameras": len(config.cameras),
        "debug": config.debug,
    }
