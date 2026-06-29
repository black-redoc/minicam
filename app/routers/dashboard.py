from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.settings import get_settings

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Serve the camera dashboard."""
    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"camera_host": settings.camera_host},
    )
