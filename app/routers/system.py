"""Device setup endpoints for minicam-mobile: Cloudflare Tunnel (ADR 0008),
the central dashboard connection (ADR 0009) and the tunnel the dashboard hands
over when pairing (ADR 0010).

Admins only, and only from the LAN: they decide how the device is reached and
who may use it from WAN, so they are never changed through the tunnel.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app import state
from app.dependencies import (
    get_cloudflare_access_verifier,
    get_entitlement_sync_repository,
    get_network_zone,
    get_tunnel_service,
    require_admin,
)
from app.models.central import (
    CentralPairRequest,
    CentralStatusResponse,
    CentralTunnelResponse,
    PairedSiteResponse,
)
from app.models.domain import User
from app.models.tunnel import TunnelSetupRequest, TunnelStatusResponse
from app.repositories.entitlement_sync_repository import EntitlementSyncRepository
from app.security_settings import get_security_settings
from app.services.central import PairedSite, PairingError, redeem_pairing_code
from app.services.central_config import (
    CentralSettings,
    read_central_file,
    remove_central_file,
    write_central_file,
)
from app.services.network import NetworkZone
from app.services.tunnel import AccessSettings, TunnelService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

_LAN_ONLY_MESSAGE = "Remote access can only be set up from the local network"
_UNAVAILABLE_MESSAGE = "This installation has no tunnel helper"
_CENTRAL_TUNNEL_MESSAGE = (
    "Remote access is managed by the central dashboard; disconnect from it first"
)
_ENV_ACCESS_MESSAGE = (
    "This device's Cloudflare Access settings are in its .env file; "
    "the dashboard's remote access was not installed"
)
_INVALID_TUNNEL_MESSAGE = "The dashboard sent remote access settings that are not valid"


def _env_access_configured() -> bool:
    return bool(
        os.environ.get("MINICAM_CF_ACCESS_TEAM_DOMAIN")
        or os.environ.get("MINICAM_CF_ACCESS_AUD")
    )


def _not_central(tunnel_service: TunnelService) -> None:
    if tunnel_service.managed_by_central:
        raise HTTPException(status.HTTP_409_CONFLICT, _CENTRAL_TUNNEL_MESSAGE)


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
        source=current.source,
    )


@router.post("/tunnel", status_code=status.HTTP_202_ACCEPTED)
async def set_up_tunnel(
    body: TunnelSetupRequest,
    user: User = Depends(_lan_admin),
    tunnel_service: TunnelService = Depends(_helper),
) -> Response:
    _not_central(tunnel_service)
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
    _not_central(tunnel_service)
    tunnel_service.request_removal()
    _reload_access_settings()
    logger.info("Tunnel removal requested by user %d", user.id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


_ENV_CONFIGURED_MESSAGE = (
    "This device's dashboard is set in its .env file; change it there instead"
)

Redeemer = Callable[[str, str], Awaitable[PairedSite]]


async def _redeem(url: str, code: str) -> PairedSite:
    async with httpx.AsyncClient() as http:
        return await redeem_pairing_code(http, url, code)


def get_pairing_redeemer() -> Redeemer:
    """Seam for tests: exchanges a pairing code with the dashboard."""
    return _redeem


def _central_source() -> str | None:
    if os.environ.get("MINICAM_CENTRAL_URL") or os.environ.get("MINICAM_DEVICE_TOKEN"):
        return "env"
    return "app" if read_central_file() is not None else None


async def _central_status(
    sync_repository: EntitlementSyncRepository,
    site: PairedSiteResponse | None = None,
    tunnel: CentralTunnelResponse | None = None,
) -> CentralStatusResponse:
    settings = get_security_settings()
    connected = settings.central_mode
    return CentralStatusResponse(
        connected=connected,
        url=settings.central_url,
        source=_central_source() if connected else None,
        last_synced_at=await sync_repository.last_synced_at() if connected else None,
        site=site,
        tunnel=tunnel,
    )


def _install_central_tunnel(
    paired: PairedSite, tunnel_service: TunnelService, user: User
) -> CentralTunnelResponse | None:
    """Install the tunnel from the pair answer with the app's path (ADR 0008),
    marked as the dashboard's. The pairing itself already holds."""
    if paired.tunnel_invalid:
        logger.warning("The dashboard sent invalid remote access settings")
        return CentralTunnelResponse(
            state="error", hostname=None, message=_INVALID_TUNNEL_MESSAGE
        )
    tunnel = paired.tunnel
    if tunnel is None:
        return None
    hostname = tunnel.access.hostname
    if _env_access_configured():
        return CentralTunnelResponse(
            state="skipped", hostname=hostname, message=_ENV_ACCESS_MESSAGE
        )
    if not tunnel_service.available:
        return CentralTunnelResponse(
            state="error", hostname=hostname, message=_UNAVAILABLE_MESSAGE
        )
    current = tunnel_service.settings()
    replaced = (
        current is not None and current.access is not None and current.source == "app"
    )
    tunnel_service.request_setup(tunnel.token, tunnel.access, source="central")
    _reload_access_settings()
    logger.info(
        "Tunnel setup for %s from the dashboard requested by user %d%s",
        hostname,
        user.id,
        " (replaces the app's tunnel)" if replaced else "",
    )
    return CentralTunnelResponse(
        state="installing", hostname=hostname, message=None, replaced=replaced
    )


@router.get("/central", response_model=CentralStatusResponse)
async def central_status(
    _: User = Depends(_lan_admin),
    sync_repository: EntitlementSyncRepository = Depends(
        get_entitlement_sync_repository
    ),
) -> CentralStatusResponse:
    return await _central_status(sync_repository)


@router.post("/central", response_model=CentralStatusResponse)
async def pair_with_central(
    body: CentralPairRequest,
    user: User = Depends(_lan_admin),
    redeem: Redeemer = Depends(get_pairing_redeemer),
    sync_repository: EntitlementSyncRepository = Depends(
        get_entitlement_sync_repository
    ),
    tunnel_service: TunnelService = Depends(get_tunnel_service),
) -> CentralStatusResponse:
    """Exchange the dashboard's pairing code for this device's token, store
    it and start syncing, without a restart. Pairing again rotates the token.
    A tunnel in the answer is installed too (ADR 0010)."""
    if _central_source() == "env":
        raise HTTPException(status.HTTP_409_CONFLICT, _ENV_CONFIGURED_MESSAGE)
    runtime = state.central_runtime
    if runtime is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Not ready yet")
    try:
        paired = await redeem(body.url, body.pairing_code)
    except PairingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    settings = CentralSettings(url=body.url, device_token=paired.device_token)
    write_central_file(settings)
    get_security_settings.cache_clear()
    await runtime.start(settings)
    logger.info(
        "Paired with the dashboard %s as site %s by user %d",
        body.url,
        paired.hostname,
        user.id,
    )
    site = PairedSiteResponse(name=paired.name, hostname=paired.hostname)
    tunnel = _install_central_tunnel(paired, tunnel_service, user)
    return await _central_status(sync_repository, site, tunnel)


@router.delete("/central", response_model=CentralStatusResponse)
async def unpair_from_central(
    user: User = Depends(_lan_admin),
    sync_repository: EntitlementSyncRepository = Depends(
        get_entitlement_sync_repository
    ),
    tunnel_service: TunnelService = Depends(get_tunnel_service),
) -> CentralStatusResponse:
    """Stop syncing and forget the dashboard; WAN access is managed on the
    device again (the Admin page's WAN checkboxes unlock). The dashboard's
    tunnel is removed too; one the app set up stays (ADR 0010)."""
    if _central_source() == "env":
        raise HTTPException(status.HTTP_409_CONFLICT, _ENV_CONFIGURED_MESSAGE)
    if state.central_runtime is not None:
        await state.central_runtime.stop()
    remove_central_file()
    get_security_settings.cache_clear()
    if tunnel_service.managed_by_central and tunnel_service.available:
        tunnel_service.request_removal()
        _reload_access_settings()
        logger.info("Tunnel removal from unpairing requested by user %d", user.id)
    logger.info("Unpaired from the dashboard by user %d", user.id)
    return await _central_status(sync_repository)
