from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import (
    get_authorization_service,
    get_optional_current_user,
    require_admin,
)
from app.models.domain import User
from app.services.authorization import AuthorizationService
from app.settings import get_config

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Serve the login form. Renders even if already authenticated — the
    client-side script redirects to '/' once a session cookie is confirmed.
    """
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/", response_model=None)
async def home(
    request: Request,
    user: User | None = Depends(get_optional_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> HTMLResponse | RedirectResponse:
    """Serve the camera dashboard, filtered to the caller's viewable cameras."""
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    config = get_config()
    viewable = set(await authorization_service.list_viewable_camera_names(user.id))
    cameras = [cam for cam in config.cameras if cam.name in viewable]
    first_camera = cameras[0].name if cameras else ""
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "cameras": cameras,
            "active_camera": first_camera,
            "is_admin": user.is_admin,
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request, _admin: User = Depends(require_admin)
) -> HTMLResponse:
    """Serve the admin page shell. All data (users, cameras, permissions)
    loads client-side via the existing /api/auth/* and /api/cameras
    endpoints — see app/static/js/admin.js.
    """
    return templates.TemplateResponse(request=request, name="admin.html")
