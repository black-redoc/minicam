"""Environment-driven configuration for the security layer (secrets, TTLs).

Mirrors the lru_cache singleton pattern used by app.settings.get_config so
both configuration sources are reset the same way in tests
(get_security_settings.cache_clear()).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

_DEFAULT_SESSION_TTL_SECONDS = 60 * 60 * 24
_DEFAULT_STREAM_TOKEN_TTL_SECONDS = 30
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "minicam.db"
_JWT_ALGORITHM = "HS256"

# Values shipped in .env.example. Running with any of them is always a
# mistake: the JWT secret is public, and the Cloudflare Access ones make every
# WAN request fail with an opaque "Invalid Cloudflare Access token" 403.
_PLACEHOLDER_VALUES: dict[str, frozenset[str]] = {
    "MINICAM_JWT_SECRET": frozenset({"changeme-generate-a-random-64-char-hex-string"}),
    "MINICAM_CF_ACCESS_TEAM_DOMAIN": frozenset({"https://myteam.cloudflareaccess.com"}),
    "MINICAM_CF_ACCESS_AUD": frozenset({"changeme-aud-tag-from-access-application"}),
}


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    jwt_secret: str
    jwt_algorithm: str = _JWT_ALGORITHM
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS
    stream_token_ttl_seconds: int = _DEFAULT_STREAM_TOKEN_TTL_SECONDS
    db_path: str = str(_DEFAULT_DB_PATH)
    trusted_proxies: frozenset[str] = field(default_factory=frozenset)
    cf_access_team_domain: str | None = None
    cf_access_aud: str | None = None
    # Central mode (minicam-admin ADR 0007): WAN entitlements come from the
    # dashboard. The token is a secret, so it is kept out of repr() and logs.
    central_url: str | None = None
    device_token: str | None = field(default=None, repr=False)

    @property
    def central_mode(self) -> bool:
        return self.central_url is not None


def _reject_placeholders() -> None:
    for env_var, placeholders in _PLACEHOLDER_VALUES.items():
        if os.environ.get(env_var, "").strip() in placeholders:
            raise RuntimeError(
                f"{env_var} still has the example value from .env.example — set "
                "the real value (see app/docs/configuration.md), or leave the "
                "Cloudflare Access ones empty if you don't use the tunnel"
            )


def _read_central(url_var: str, token_var: str) -> tuple[str | None, str | None]:
    """Both set (HTTPS, or plain HTTP to localhost for development) or neither."""
    url = os.environ.get(url_var, "").strip() or None
    token = os.environ.get(token_var, "").strip() or None
    if url is None and token is None:
        return None, None
    if url is None or token is None:
        raise RuntimeError(
            f"{url_var} and {token_var} must be set together to fetch WAN "
            "entitlements from the dashboard, or both left empty"
        )
    parts = urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
    if not (parts.scheme == "https" or local) or not parts.hostname:
        raise RuntimeError(f"{url_var} must be an https:// URL")
    return url.rstrip("/"), token


def _read_int(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    return int(raw) if raw else default


def _read_trusted_proxies(env_var: str) -> frozenset[str]:
    raw = os.environ.get(env_var, "")
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


@lru_cache
def get_security_settings() -> SecuritySettings:
    """Build settings from the environment.

    Raises:
        RuntimeError: if MINICAM_JWT_SECRET is not set — there is no safe
            default for a token-signing secret — if any setting still has
            its .env.example placeholder value, or if only one of
            MINICAM_CENTRAL_URL / MINICAM_DEVICE_TOKEN is set or the URL is
            not HTTPS.
    """
    _reject_placeholders()
    secret = os.environ.get("MINICAM_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "MINICAM_JWT_SECRET is not set — required to sign session and stream tokens"
        )
    central_url, device_token = _read_central(
        "MINICAM_CENTRAL_URL", "MINICAM_DEVICE_TOKEN"
    )
    return SecuritySettings(
        jwt_secret=secret,
        session_ttl_seconds=_read_int(
            "MINICAM_SESSION_TTL_SECONDS", _DEFAULT_SESSION_TTL_SECONDS
        ),
        stream_token_ttl_seconds=_read_int(
            "MINICAM_STREAM_TOKEN_TTL_SECONDS", _DEFAULT_STREAM_TOKEN_TTL_SECONDS
        ),
        db_path=os.environ.get("MINICAM_DB_PATH", str(_DEFAULT_DB_PATH)),
        trusted_proxies=_read_trusted_proxies("MINICAM_TRUSTED_PROXIES"),
        cf_access_team_domain=os.environ.get("MINICAM_CF_ACCESS_TEAM_DOMAIN") or None,
        cf_access_aud=os.environ.get("MINICAM_CF_ACCESS_AUD") or None,
        central_url=central_url,
        device_token=device_token,
    )
