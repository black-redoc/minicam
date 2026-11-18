"""Pydantic I/O models for minicam updates (ADR 0013)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.services.update import UpdateState, parse_tag


class UpdateStatusResponse(BaseModel):
    available: bool
    current: str
    latest: str | None
    update_available: bool
    state: UpdateState
    version: str | None
    message: str | None
    checked_at: str | None


class UpdateRequest(BaseModel):
    version: str

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if parse_tag(value) is None:
            raise ValueError("Not a vX.Y.Z release")
        return value
