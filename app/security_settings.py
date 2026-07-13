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


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    jwt_secret: str
    jwt_algorithm: str = _JWT_ALGORITHM
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS
    stream_token_ttl_seconds: int = _DEFAULT_STREAM_TOKEN_TTL_SECONDS
    db_path: str = str(_DEFAULT_DB_PATH)
    trusted_proxies: frozenset[str] = field(default_factory=frozenset)


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
            default for a token-signing secret.
    """
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
    )
