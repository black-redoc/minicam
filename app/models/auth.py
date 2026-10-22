"""Pydantic I/O models for the auth API surface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserPublic(BaseModel):
    """Safe user projection — never includes password_hash."""

    id: int
    email: str
    is_admin: bool


class StreamTokenResponse(BaseModel):
    token: str
    expires_in: int


class CreateUserRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    is_admin: bool = False


class FirstAdminRequest(BaseModel):
    """First-run setup: the account created while no user exists yet."""

    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)


class SetupStatusResponse(BaseModel):
    setup_required: bool


class SetPermissionRequest(BaseModel):
    user_id: int
    camera_name: str = Field(..., min_length=1)
    can_view: bool = False
    can_use_ptz: bool = False


class CameraPermissionPublic(BaseModel):
    user_id: int
    camera_name: str
    can_view: bool
    can_use_ptz: bool


class ViewableCamera(BaseModel):
    """A camera the caller can view, for native clients (GET /api/me/cameras)."""

    name: str
    can_use_ptz: bool
