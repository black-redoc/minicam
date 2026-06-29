import logging

from fastapi import FastAPI

from app.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="minicam",
    description="IP Camera Dashboard",
    version="0.1.0",
)


@app.get("/")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "message": "minicam running"}


@app.get("/health")
def health() -> dict[str, str | bool]:
    """Detailed health status."""
    settings = get_settings()
    return {
        "status": "ok",
        "camera_host": settings.camera_host,
        "debug": settings.debug,
    }
