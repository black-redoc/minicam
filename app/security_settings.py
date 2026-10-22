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


def _reject_placeholders() -> None:
    for env_var, placeholders in _PLACEHOLDER_VALUES.items():
        if os.environ.get(env_var, "").strip() in placeholders:
            raise RuntimeError(
                f"{env_var} still has the example value from .env.example — set "
                "the real value (see app/docs/configuration.md), or leave the "
                "Cloudflare Access ones empty if you don't use the tunnel"
            )


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
            default for a token-signing secret — or if any setting still has
            its .env.example placeholder value.
    """
    _reject_placeholders()
    secret = os.environ.get("MINICAM_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "MINICAM_JWT_SECRET is not set — required to sign session and stream tokens"
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
    )
