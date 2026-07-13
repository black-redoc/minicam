"""Classifies an inbound request as LAN or WAN.

No real Cloudflare Tunnel exists yet (see plan), so `trusted_proxies` is
empty by default and every request falls through to the direct-IP check.
Once `cloudflared` runs on the same Pi, filling in
MINICAM_TRUSTED_PROXIES is the only change needed here.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum

from fastapi import Request


class NetworkZone(StrEnum):
    LAN = "lan"
    WAN = "wan"


def classify_network(
    request: Request, trusted_proxies: frozenset[str] = frozenset()
) -> NetworkZone:
    """Classify the request's origin.

    Fail-safe: any request that cannot be confidently placed on the LAN
    (unknown client IP, unparsable address, or arriving through a trusted
    proxy — which only forwards WAN traffic) is classified as WAN, matching
    the deny-by-default posture used everywhere else in this layer.
    """
    client_host = request.client.host if request.client else None

    if client_host is None or client_host in trusted_proxies:
        return NetworkZone.WAN

    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return NetworkZone.WAN

    if client_ip.is_private or client_ip.is_loopback:
        return NetworkZone.LAN

    return NetworkZone.WAN
