"""Pydantic I/O models for the central dashboard pairing API (ADR 0009)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.central_config import normalize_central_url


class CentralPairRequest(BaseModel):
    url: str
    # The dashboard's one-time code (`ABCD-EFGH`); it checks the code itself.
    pairing_code: str = Field(
        ..., min_length=4, max_length=32, pattern=r"^[A-Za-z0-9 -]+$", repr=False
    )

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        normalized = normalize_central_url(value)
        if normalized is None:
            raise ValueError("Must be the dashboard's https:// address")
        return normalized


class PairedSiteResponse(BaseModel):
    name: str
    hostname: str


class CentralStatusResponse(BaseModel):
    connected: bool
    url: str | None
    # "env": set in .env (not changeable from the app); "app": paired from the app.
    source: Literal["env", "app"] | None
    last_synced_at: str | None
    site: PairedSiteResponse | None = None
