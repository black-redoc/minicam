"""Tests for classify_network: parametrized LAN/WAN/fail-safe table."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request

from app.services.network import NetworkZone, classify_network


def _make_request(client_host: str | None) -> Request:
    request = MagicMock(spec=Request)
    if client_host is None:
        request.client = None
    else:
        request.client = MagicMock(host=client_host)
    return request


@pytest.mark.parametrize(
    ("client_host", "expected_zone"),
    [
        ("192.168.1.50", NetworkZone.LAN),
        ("10.0.0.5", NetworkZone.LAN),
        ("172.16.0.1", NetworkZone.LAN),
        ("127.0.0.1", NetworkZone.LAN),
        ("::1", NetworkZone.LAN),
        ("8.8.8.8", NetworkZone.WAN),
        ("1.1.1.1", NetworkZone.WAN),
        ("not-an-ip", NetworkZone.WAN),
        (None, NetworkZone.WAN),
    ],
)
def test_classify_network_by_direct_ip(
    client_host: str | None, expected_zone: NetworkZone
) -> None:
    request = _make_request(client_host)
    assert classify_network(request) is expected_zone


def test_trusted_proxy_ip_is_always_wan_even_if_private() -> None:
    # A private IP that belongs to the local cloudflared tunnel proxy is
    # forwarding real WAN traffic — it must never be classified as LAN.
    request = _make_request("192.168.1.1")
    assert (
        classify_network(request, trusted_proxies=frozenset({"192.168.1.1"}))
        is NetworkZone.WAN
    )


def test_untrusted_private_ip_stays_lan_when_trusted_proxies_configured() -> None:
    request = _make_request("192.168.1.50")
    assert (
        classify_network(request, trusted_proxies=frozenset({"192.168.1.1"}))
        is NetworkZone.LAN
    )
