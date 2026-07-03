from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.settings import get_config

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Serve the camera dashboard."""
    config = get_config()
    cameras = config.cameras
    first_camera = cameras[0].name if cameras else ""
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"cameras": cameras, "active_camera": first_camera},
    )
