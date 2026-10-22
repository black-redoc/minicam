"""Row-shaped domain objects for the security layer.

These are plain immutable dataclasses, not Pydantic models: they represent
rows already validated by SQLite's own CHECK constraints, not untrusted I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    password_hash: str
    is_admin: bool
    is_active: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Session:
    jti: str
    user_id: int
    issued_at: str
    expires_at: str
    revoked_at: str | None
    user_agent: str | None
    ip_address: str | None


@dataclass(frozen=True, slots=True)
class CameraPermission:
    id: int
    user_id: int
    camera_name: str
    can_view: bool
    can_use_ptz: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class WanAccess:
    user_id: int
    is_active: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class PushSubscription:
    id: int
    user_id: int
    endpoint: str
    p256dh: str
    auth: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Recording:
    id: int
    camera_name: str
    file_name: str
    started_at: str
    duration_seconds: float
    size_bytes: int
    classes: tuple[str, ...]
    created_at: str
