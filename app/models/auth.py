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
    # MIME type with codecs of the camera's own stream (format=fmp4), or
    # None while it isn't known yet: the client then uses MJPEG.
    video_mime: str | None = None
    # Same, with the camera's audio (format=fmp4&audio=true): None unless
    # the camera sends supported audio and the caller has can_listen.
    audio_mime: str | None = None


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
    can_listen: bool = False


class SetWanAccessRequest(BaseModel):
    user_id: int
    is_active: bool


class WanAccessPublic(BaseModel):
    """A user's WAN gate; no row in wan_access reads as is_active=False."""

    user_id: int
    is_active: bool


class CameraPermissionPublic(BaseModel):
    user_id: int
    camera_name: str
    can_view: bool
    can_use_ptz: bool
    can_listen: bool


class ViewableCamera(BaseModel):
    """A camera the caller can view, for native clients (GET /api/me/cameras)."""

    name: str
    can_use_ptz: bool
    can_listen: bool
