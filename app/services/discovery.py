"""WS-Discovery service for locating ONVIF cameras on the local network."""

from __future__ import annotations

import logging
from typing import cast
from urllib.parse import urlparse

from wsdiscovery import QName, WSDiscovery

from app.models.config import DiscoveredCamera

logger = logging.getLogger(__name__)

_ONVIF_NS = "http://www.onvif.org/ver10/network/wsdl"
_ONVIF_SCOPE_BASE = "onvif://www.onvif.org/"
_SCOPE_NAME = "onvif://www.onvif.org/name/"
_SCOPE_HARDWARE = "onvif://www.onvif.org/hardware/"
_SCOPE_LOCATION = "onvif://www.onvif.org/location/"


def _scope_value(scope: object) -> str:
    """Extract the string value from a Scope object or plain string."""
    if hasattr(scope, "getValue"):
        return cast(str, scope.getValue())
    return str(scope)


def _extract_scopes(scopes: list[object]) -> tuple[str, str, str]:
    """Parse name, hardware, and location from ONVIF scope URIs."""
    name = hardware = location = ""
    for scope in scopes:
        value = _scope_value(scope)
        if value.startswith(_SCOPE_NAME):
            name = value[len(_SCOPE_NAME) :]
        elif value.startswith(_SCOPE_HARDWARE):
            hardware = value[len(_SCOPE_HARDWARE) :]
        elif value.startswith(_SCOPE_LOCATION):
            location = value[len(_SCOPE_LOCATION) :]
    return name, hardware, location


def _extract_host(xaddrs: list[str]) -> str:
    """Parse the host from the first XAddr URL."""
    parsed = urlparse(xaddrs[0])
    return parsed.hostname or ""


def discover_onvif_cameras(timeout: int = 5) -> list[DiscoveredCamera]:
    """Probe the local network for ONVIF cameras via WS-Discovery.

    Args:
        timeout: Seconds to wait for probe responses.

    Returns:
        List of discovered cameras. Empty on failure or no results.
    """
    try:
        wsd = WSDiscovery()
        wsd.start()
        ttype = QName(_ONVIF_NS, "NetworkVideoTransmitter")
        services = wsd.searchServices(types=[ttype], timeout=timeout)
        wsd.stop()
    except Exception:
        logger.exception("WS-Discovery probe failed")
        return []

    cameras: list[DiscoveredCamera] = []
    for svc in services:
        xaddrs = svc.getXAddrs()
        if not xaddrs:
            # Cannot address a service with no XAddrs
            continue
        host = _extract_host(xaddrs)
        if not host:
            continue
        name, hardware, location = _extract_scopes(svc.getScopes())
        cameras.append(
            DiscoveredCamera(
                host=host,
                name=name,
                hardware=hardware,
                location=location,
            )
        )

    return cameras
