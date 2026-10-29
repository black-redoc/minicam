"""Pydantic I/O models for the Cloudflare Tunnel API (ADR 0008)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.services.tunnel import (
    TunnelState,
    is_valid_aud,
    is_valid_hostname,
    is_valid_team_domain,
    is_valid_tunnel_token,
)


class TunnelSetupRequest(BaseModel):
    # repr=False: the token is a credential for the tunnel, kept out of
    # validation errors and logs.
    tunnel_token: str = Field(..., repr=False)
    hostname: str
    team_domain: str
    aud: str

    @field_validator("tunnel_token")
    @classmethod
    def _check_token(cls, value: str) -> str:
        if not is_valid_tunnel_token(value):
            raise ValueError("Not a Cloudflare Tunnel token")
        return value

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value: str) -> str:
        if not is_valid_hostname(value):
            raise ValueError("Not a valid hostname")
        return value

    @field_validator("team_domain")
    @classmethod
    def _check_team_domain(cls, value: str) -> str:
        if not is_valid_team_domain(value):
            raise ValueError("Must be https://<team>.cloudflareaccess.com")
        return value

    @field_validator("aud")
    @classmethod
    def _check_aud(cls, value: str) -> str:
        if not is_valid_aud(value):
            raise ValueError("Must be the Access application's 64-character AUD tag")
        return value


class TunnelStatusResponse(BaseModel):
    available: bool
    state: TunnelState
    hostname: str | None
    message: str | None
