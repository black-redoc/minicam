"""Baseline HTTP security headers for every response.

Content-Security-Policy allows the Tailwind CDN script and the small inline
bootstrap scripts already used in app/templates (see base.html/home.html/
login.html) plus data:/blob: images — the blank-frame placeholder and the
live camera frames in app/static/js/grid.js and stream.js render through
those schemes. Tightening this further (nonces, dropping the CDN, self-
hosting Tailwind) is a template-level change out of scope here.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.security_settings import get_security_settings
from app.services.network import NetworkZone, classify_network

_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)
_HSTS_VALUE = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds nosniff/frame-options/referrer/CSP headers to every response,
    plus Strict-Transport-Security on WAN requests only.

    HSTS is deliberately withheld on LAN: this app is also served over
    plain HTTP on the LAN on purpose (see app/routers/auth.py), and HSTS
    would make browsers refuse plain HTTP there on every later visit.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
        if self._is_wan_request(request):
            response.headers["Strict-Transport-Security"] = _HSTS_VALUE
        return response

    @staticmethod
    def _is_wan_request(request: Request) -> bool:
        settings = get_security_settings()
        zone = classify_network(request, trusted_proxies=settings.trusted_proxies)
        return zone is NetworkZone.WAN
