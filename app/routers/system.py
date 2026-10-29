"""Device setup endpoints for minicam-mobile: Cloudflare Tunnel (ADR 0008).

Admins only, and only from the LAN: the tunnel is what makes the device
reachable from WAN, so it is never changed through itself.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.dependencies import (
    get_cloudflare_access_verifier,
    get_network_zone,
    get_tunnel_service,
    require_admin,
)
from app.models.domain import User
from app.models.tunnel import TunnelSetupRequest, TunnelStatusResponse
from app.security_settings import get_security_settings
from app.services.network import NetworkZone
from app.services.tunnel import AccessSettings, TunnelService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

_LAN_ONLY_MESSAGE = "Remote access can only be set up from the local network"
_UNAVAILABLE_MESSAGE = "This installation has no tunnel helper"


def _lan_admin(
    user: User = Depends(require_admin),
    network_zone: NetworkZone = Depends(get_network_zone),
) -> User:
    if network_zone is not NetworkZone.LAN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _LAN_ONLY_MESSAGE)
    return user


def _helper(
    _: User = Depends(_lan_admin),
    tunnel_service: TunnelService = Depends(get_tunnel_service),
) -> TunnelService:
    if not tunnel_service.available:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _UNAVAILABLE_MESSAGE)
    return tunnel_service


def _reload_access_settings() -> None:
    # Both are lru_cache singletons built from the settings: drop them so the
    # next request verifies Access tokens against the new team domain/AUD.
    get_security_settings.cache_clear()
    get_cloudflare_access_verifier.cache_clear()


@router.get("/tunnel", response_model=TunnelStatusResponse)
async def tunnel_status(
    tunnel_service: TunnelService = Depends(_helper),
) -> TunnelStatusResponse:
    current = tunnel_service.status()
    return TunnelStatusResponse(
        available=current.available,
        state=current.state,
        hostname=current.hostname,
        message=current.message,
    )


@router.post("/tunnel", status_code=status.HTTP_202_ACCEPTED)
async def set_up_tunnel(
    body: TunnelSetupRequest,
    user: User = Depends(_lan_admin),
    tunnel_service: TunnelService = Depends(_helper),
) -> Response:
    access = AccessSettings(
        hostname=body.hostname, team_domain=body.team_domain, aud=body.aud
    )
    tunnel_service.request_setup(body.tunnel_token, access)
    _reload_access_settings()
    logger.info("Tunnel setup for %s requested by user %d", body.hostname, user.id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.delete("/tunnel", status_code=status.HTTP_202_ACCEPTED)
async def remove_tunnel(
    user: User = Depends(_lan_admin),
    tunnel_service: TunnelService = Depends(_helper),
) -> Response:
    tunnel_service.request_removal()
    _reload_access_settings()
    logger.info("Tunnel removal requested by user %d", user.id)
    return Response(status_code=status.HTTP_202_ACCEPTED)
