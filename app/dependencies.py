"""FastAPI dependency wiring for the security layer: the single place that
turns repositories + services into `Depends()`-usable callables.
"""

from __future__ import annotations

import aiosqlite
from fastapi import Cookie, Depends, HTTPException, Request, status

from app.db.connection import get_connection
from app.models.domain import User
from app.repositories.permission_repository import (
    PermissionRepository,
    SQLitePermissionRepository,
)
from app.repositories.session_repository import (
    SessionRepository,
    SQLiteSessionRepository,
)
from app.repositories.user_repository import SQLiteUserRepository, UserRepository
from app.repositories.wan_access_repository import (
    SQLiteWanAccessRepository,
    WanAccessRepository,
)
from app.security_settings import SecuritySettings, get_security_settings
from app.services.auth import InvalidSessionTokenError, PasswordHasher, TokenService
from app.services.authorization import AuthorizationService
from app.services.network import NetworkZone, classify_network
from app.services.stream_tokens import StreamTokenService
from app.services.wan_access import WanAccessGateService

SESSION_COOKIE_NAME = "minicam_session"


def get_db_connection() -> aiosqlite.Connection:
    return get_connection()


def get_user_repository(
    conn: aiosqlite.Connection = Depends(get_db_connection),
) -> UserRepository:
    return SQLiteUserRepository(conn)


def get_session_repository(
    conn: aiosqlite.Connection = Depends(get_db_connection),
) -> SessionRepository:
    return SQLiteSessionRepository(conn)


def get_permission_repository(
    conn: aiosqlite.Connection = Depends(get_db_connection),
) -> PermissionRepository:
    return SQLitePermissionRepository(conn)


def get_wan_access_repository(
    conn: aiosqlite.Connection = Depends(get_db_connection),
) -> WanAccessRepository:
    return SQLiteWanAccessRepository(conn)


def get_password_hasher() -> PasswordHasher:
    return PasswordHasher()


def get_token_service(
    settings: SecuritySettings = Depends(get_security_settings),
) -> TokenService:
    return TokenService(secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_stream_token_service(
    settings: SecuritySettings = Depends(get_security_settings),
) -> StreamTokenService:
    return StreamTokenService(
        secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


def get_authorization_service(
    permission_repository: PermissionRepository = Depends(get_permission_repository),
) -> AuthorizationService:
    return AuthorizationService(permission_repository)


def get_wan_access_gate_service(
    wan_access_repository: WanAccessRepository = Depends(get_wan_access_repository),
) -> WanAccessGateService:
    return WanAccessGateService(wan_access_repository)


def get_network_zone(request: Request) -> NetworkZone:
    settings = get_security_settings()
    return classify_network(request, trusted_proxies=settings.trusted_proxies)


async def get_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    token_service: TokenService = Depends(get_token_service),
    session_repository: SessionRepository = Depends(get_session_repository),
    user_repository: UserRepository = Depends(get_user_repository),
) -> User:
    if session_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        payload = token_service.verify(session_token)
    except InvalidSessionTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from exc

    session = await session_repository.get(payload.jti)
    if session is None or session.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")

    user = await user_repository.get_by_id(payload.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    return user


async def get_optional_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    token_service: TokenService = Depends(get_token_service),
    session_repository: SessionRepository = Depends(get_session_repository),
    user_repository: UserRepository = Depends(get_user_repository),
) -> User | None:
    if session_token is None:
        return None
    try:
        return await get_current_user(
            session_token, token_service, session_repository, user_repository
        )
    except HTTPException:
        return None


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return user


async def require_can_view(
    camera_name: str,
    user: User = Depends(get_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> User:
    # 404, not 403: a user without access must not learn the camera exists.
    if not await authorization_service.can_view(user.id, camera_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


async def require_can_use_ptz(
    camera_name: str,
    user: User = Depends(get_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> User:
    if not await authorization_service.can_use_ptz(user.id, camera_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


async def enforce_wan_access_gate(
    user: User = Depends(get_current_user),
    network_zone: NetworkZone = Depends(get_network_zone),
    wan_access_gate: WanAccessGateService = Depends(get_wan_access_gate_service),
) -> None:
    if network_zone is NetworkZone.LAN:
        return
    if not await wan_access_gate.is_active(user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "WAN access is not enabled for this account"
        )
