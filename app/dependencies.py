"""FastAPI dependency wiring for the security layer: the single place that
turns repositories + services into `Depends()`-usable callables.
"""

from __future__ import annotations

from functools import lru_cache

import aiosqlite
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status
from starlette.requests import HTTPConnection

from app.db.connection import get_connection
from app.models.domain import User
from app.repositories.permission_repository import (
    PermissionRepository,
    SQLitePermissionRepository,
)
from app.repositories.push_subscription_repository import (
    PushSubscriptionRepository,
    SQLitePushSubscriptionRepository,
)
from app.repositories.recording_repository import (
    RecordingRepository,
    SQLiteRecordingRepository,
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
from app.services.cloudflare_access import (
    CloudflareAccessVerifier,
    InvalidAccessTokenError,
)
from app.services.login_rate_limiter import LoginRateLimiter
from app.services.network import NetworkZone, classify_network
from app.services.stream_access import StreamAccessService
from app.services.stream_tokens import StreamTokenService
from app.services.wan_access import WanAccessGateService

SESSION_COOKIE_NAME = "minicam_session"
_CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
_CF_ACCESS_JWKS_PATH = "/cdn-cgi/access/certs"


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


def get_push_subscription_repository(
    conn: aiosqlite.Connection = Depends(get_db_connection),
) -> PushSubscriptionRepository:
    return SQLitePushSubscriptionRepository(conn)


def get_recording_repository(
    conn: aiosqlite.Connection = Depends(get_db_connection),
) -> RecordingRepository:
    return SQLiteRecordingRepository(conn)


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


def get_stream_access_service(
    session_repository: SessionRepository = Depends(get_session_repository),
    user_repository: UserRepository = Depends(get_user_repository),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    wan_access_gate: WanAccessGateService = Depends(get_wan_access_gate_service),
) -> StreamAccessService:
    return StreamAccessService(
        session_repository, user_repository, authorization_service, wan_access_gate
    )


@lru_cache
def get_login_rate_limiter() -> LoginRateLimiter:
    """Build the singleton login rate limiter.

    Must be a single shared instance (lru_cache, like get_security_settings)
    since it holds in-memory attempt counters that need to persist across
    requests within this process.
    """
    return LoginRateLimiter()


def get_network_zone(request: HTTPConnection) -> NetworkZone:
    settings = get_security_settings()
    return classify_network(request, trusted_proxies=settings.trusted_proxies)


@lru_cache
def get_cloudflare_access_verifier() -> CloudflareAccessVerifier | None:
    """Build the Cloudflare Access verifier, or None if it is not configured.

    Returns None instead of raising so this stays safe to resolve for every
    request — including LAN ones, which never use it. enforce_wan_access_gate
    is the one that turns "not configured" into a fail-closed 403, and only
    for WAN traffic that actually needs it.
    """
    settings = get_security_settings()
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        return None
    jwks_uri = settings.cf_access_team_domain + _CF_ACCESS_JWKS_PATH
    return CloudflareAccessVerifier(
        jwks_client=jwt.PyJWKClient(jwks_uri),
        audience=settings.cf_access_aud,
        issuer=settings.cf_access_team_domain,
    )


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


def get_session_jti(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    token_service: TokenService = Depends(get_token_service),
) -> str:
    """The current session's jti. Resolve after get_current_user, which has
    already checked the session is valid and not revoked."""
    if session_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        return token_service.verify(session_token).jti
    except InvalidSessionTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from exc


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


def _require_matching_access_identity(
    request: Request,
    user: User,
    access_verifier: CloudflareAccessVerifier | None,
) -> None:
    """Fail closed unless a valid Cloudflare Access JWT names this user."""
    if access_verifier is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cloudflare Access is not configured for WAN requests",
        )

    access_token = request.headers.get(_CF_ACCESS_JWT_HEADER)
    if access_token is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Missing Cloudflare Access token"
        )

    try:
        identity = access_verifier.verify(access_token)
    except InvalidAccessTokenError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Invalid Cloudflare Access token"
        ) from exc

    if identity.email != user.email:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cloudflare Access identity does not match the session user",
        )


async def enforce_wan_access_gate(
    request: Request,
    user: User = Depends(get_current_user),
    network_zone: NetworkZone = Depends(get_network_zone),
    wan_access_gate: WanAccessGateService = Depends(get_wan_access_gate_service),
    access_verifier: CloudflareAccessVerifier | None = Depends(
        get_cloudflare_access_verifier
    ),
) -> None:
    if network_zone is NetworkZone.LAN:
        return
    if not await wan_access_gate.is_active(user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "WAN access is not enabled for this account"
        )
    _require_matching_access_identity(request, user, access_verifier)
