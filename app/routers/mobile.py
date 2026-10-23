"""Endpoints for the native mobile clients (minicam-mobile).

The browser dashboard gets its camera list server-rendered and its
Cloudflare Access session as a cookie; a native app can do neither, so this
router gives it a JSON camera list and a way to obtain the Access token
(see minicam-mobile docs/adr/0002 and docs/specs/002).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.dependencies import (
    get_authorization_service,
    get_cloudflare_access_verifier,
    get_current_user,
    get_network_zone,
)
from app.models.auth import ViewableCamera
from app.models.domain import User
from app.services.authorization import AuthorizationService
from app.services.cloudflare_access import (
    CloudflareAccessVerifier,
    InvalidAccessTokenError,
)
from app.services.network import NetworkZone
from app.settings import get_config

router = APIRouter(tags=["mobile"])

_CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
# Fixed server-side: the client never chooses where the token is sent, so
# this endpoint can't be turned into an open redirect.
_ACCESS_CALLBACK_URI = "minicam://access-callback"
_STATE_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}")


@router.get("/api/me/cameras", response_model=list[ViewableCamera])
async def list_my_cameras(
    user: User = Depends(get_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[ViewableCamera]:
    """Cameras the caller can view, in config order — same filter as the dashboard."""
    permissions = {
        p.camera_name: p
        for p in await authorization_service.list_viewable_permissions(user.id)
    }
    return [
        ViewableCamera(
            name=cam.name,
            can_use_ptz=permissions[cam.name].can_use_ptz,
            can_listen=permissions[cam.name].can_listen,
        )
        for cam in get_config().cameras
        if cam.name in permissions
    ]


@router.get("/api/mobile/access-handoff")
async def access_handoff(
    request: Request,
    state: str = Query(...),
    network_zone: NetworkZone = Depends(get_network_zone),
    access_verifier: CloudflareAccessVerifier | None = Depends(
        get_cloudflare_access_verifier
    ),
) -> RedirectResponse:
    """Hand the verified Cloudflare Access token back to the mobile app.

    Opened in the system browser after Access has logged the user in at the
    edge. The token goes in the URL fragment so it never reaches a server
    log; it grants nothing on its own — the app still needs a minicam
    session, exactly like the browser.
    """
    if not _STATE_PATTERN.fullmatch(state):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid state")
    if network_zone is NetworkZone.LAN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Access handoff is only available over WAN"
        )
    if access_verifier is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cloudflare Access is not configured for WAN requests",
        )

    access_token = request.headers.get(_CF_ACCESS_JWT_HEADER)
    if access_token is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Missing Cloudflare Access token"
        )
    try:
        access_verifier.verify(access_token)
    except InvalidAccessTokenError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Invalid Cloudflare Access token"
        ) from exc

    return RedirectResponse(
        url=f"{_ACCESS_CALLBACK_URI}?state={state}#token={access_token}",
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store"},
    )
