"""Auth API: login/logout/me, plus admin-only user and permission management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.dependencies import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_login_rate_limiter,
    get_network_zone,
    get_password_hasher,
    get_permission_repository,
    get_session_repository,
    get_token_service,
    get_user_repository,
    require_admin,
)
from app.models.auth import (
    CameraPermissionPublic,
    CreateUserRequest,
    FirstAdminRequest,
    LoginRequest,
    SetPermissionRequest,
    SetupStatusResponse,
    UserPublic,
)
from app.models.domain import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.security_settings import SecuritySettings, get_security_settings
from app.services.auth import InvalidSessionTokenError, PasswordHasher, TokenService
from app.services.login_rate_limiter import LoginRateLimiter
from app.services.network import NetworkZone

router = APIRouter(prefix="/api/auth", tags=["auth"])

_INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"
_TOO_MANY_ATTEMPTS_MESSAGE = "Too many login attempts, try again later"
_SETUP_DONE_MESSAGE = "Setup already completed"
_SETUP_LAN_ONLY_MESSAGE = "First-run setup is only available from the local network"


def _iso8601_after(seconds: int) -> str:
    """Format now() + seconds to match the migration's TEXT timestamp style."""
    dt = datetime.now(UTC) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _to_public(user: User) -> UserPublic:
    return UserPublic(id=user.id, email=user.email, is_admin=user.is_admin)


@router.post("/login", response_model=UserPublic)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    user_repository: UserRepository = Depends(get_user_repository),
    session_repository: SessionRepository = Depends(get_session_repository),
    password_hasher: PasswordHasher = Depends(get_password_hasher),
    token_service: TokenService = Depends(get_token_service),
    settings: SecuritySettings = Depends(get_security_settings),
    rate_limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
    network_zone: NetworkZone = Depends(get_network_zone),
) -> UserPublic:
    """Verify credentials, open a revocable session, and set the session cookie."""
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_key = LoginRateLimiter.make_key(client_ip, body.email)
    if not await rate_limiter.is_allowed(rate_limit_key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, _TOO_MANY_ATTEMPTS_MESSAGE
        )

    user = await user_repository.get_by_email(body.email)
    if (
        user is None
        or not user.is_active
        or not password_hasher.verify(body.password, user.password_hash)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _INVALID_CREDENTIALS_MESSAGE)

    await _open_session(
        user,
        request,
        response,
        session_repository,
        token_service,
        settings,
        network_zone,
    )
    return _to_public(user)


@router.get("/setup", response_model=SetupStatusResponse)
async def setup_status(
    user_repository: UserRepository = Depends(get_user_repository),
) -> SetupStatusResponse:
    """Report whether first-run setup is still pending (no user exists yet)."""
    return SetupStatusResponse(setup_required=await user_repository.count_users() == 0)


@router.post("/setup", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def complete_setup(
    body: FirstAdminRequest,
    request: Request,
    response: Response,
    user_repository: UserRepository = Depends(get_user_repository),
    session_repository: SessionRepository = Depends(get_session_repository),
    password_hasher: PasswordHasher = Depends(get_password_hasher),
    token_service: TokenService = Depends(get_token_service),
    settings: SecuritySettings = Depends(get_security_settings),
    network_zone: NetworkZone = Depends(get_network_zone),
) -> UserPublic:
    """Create the first admin account and sign it in.

    Only works while the users table is empty, and only from the LAN: an
    unconfigured device must not be claimable by whoever reaches it first
    through the tunnel. Once any account exists this returns 409 forever.
    """
    if network_zone is not NetworkZone.LAN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _SETUP_LAN_ONLY_MESSAGE)

    user = await user_repository.create_first_admin(
        body.email, password_hasher.hash(body.password)
    )
    if user is None:
        raise HTTPException(status.HTTP_409_CONFLICT, _SETUP_DONE_MESSAGE)

    await _open_session(
        user,
        request,
        response,
        session_repository,
        token_service,
        settings,
        network_zone,
    )
    return _to_public(user)


async def _open_session(
    user: User,
    request: Request,
    response: Response,
    session_repository: SessionRepository,
    token_service: TokenService,
    settings: SecuritySettings,
    network_zone: NetworkZone,
) -> None:
    """Persist a revocable session for user and set the session cookie."""
    client_ip = request.client.host if request.client else "unknown"
    jti = uuid.uuid4().hex
    expires_at = _iso8601_after(settings.session_ttl_seconds)
    await session_repository.create(
        jti, user.id, expires_at, request.headers.get("user-agent"), client_ip
    )
    token = token_service.issue(user.id, jti, settings.session_ttl_seconds)

    # secure is tied to network_zone, not hardcoded: on WAN the browser always
    # sees HTTPS (terminated by Cloudflare in front of the tunnel), so the
    # cookie can and should require it. On LAN this app is deliberately also
    # served over plain HTTP (see the threat model in the security plan), and
    # a Secure cookie would silently stop being sent there.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=(network_zone is NetworkZone.WAN),
        path="/",
    )


@router.post("/logout")
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    token_service: TokenService = Depends(get_token_service),
    session_repository: SessionRepository = Depends(get_session_repository),
) -> dict[str, str]:
    """Revoke the current session (if any) and clear the cookie. Idempotent."""
    if session_token is not None:
        try:
            payload = token_service.verify(session_token)
        except InvalidSessionTokenError:
            payload = None
        if payload is not None:
            await session_repository.revoke(payload.jti)

    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)) -> UserPublic:
    return _to_public(user)


@router.post(
    "/users",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_user(
    body: CreateUserRequest,
    user_repository: UserRepository = Depends(get_user_repository),
    password_hasher: PasswordHasher = Depends(get_password_hasher),
) -> UserPublic:
    """Create a new account. Admin-only: there is no self-service signup."""
    if await user_repository.get_by_email(body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = await user_repository.create(
        body.email, password_hasher.hash(body.password), body.is_admin
    )
    return _to_public(user)


@router.get(
    "/users",
    response_model=list[UserPublic],
    dependencies=[Depends(require_admin)],
)
async def list_users(
    user_repository: UserRepository = Depends(get_user_repository),
) -> list[UserPublic]:
    """List every account. Admin-only — feeds the admin permissions page."""
    users = await user_repository.list_all()
    return [_to_public(u) for u in users]


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    user_repository: UserRepository = Depends(get_user_repository),
) -> None:
    """Delete a user account. Admin-only. Cannot delete yourself or the last admin."""
    if user_id == current_user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Cannot delete your own account"
        )

    target = await user_repository.get_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if target.is_admin and await user_repository.count_admins() <= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot delete the last remaining admin"
        )

    await user_repository.delete(user_id)


@router.post(
    "/permissions",
    response_model=CameraPermissionPublic,
    dependencies=[Depends(require_admin)],
)
async def set_permission(
    body: SetPermissionRequest,
    permission_repository: PermissionRepository = Depends(get_permission_repository),
) -> CameraPermissionPublic:
    """Grant or update a user's view/PTZ permission for a single camera."""
    permission = await permission_repository.upsert(
        body.user_id, body.camera_name, body.can_view, body.can_use_ptz
    )
    return CameraPermissionPublic(
        user_id=permission.user_id,
        camera_name=permission.camera_name,
        can_view=permission.can_view,
        can_use_ptz=permission.can_use_ptz,
    )


@router.get(
    "/permissions",
    response_model=list[CameraPermissionPublic],
    dependencies=[Depends(require_admin)],
)
async def list_permissions(
    user_id: int,
    permission_repository: PermissionRepository = Depends(get_permission_repository),
) -> list[CameraPermissionPublic]:
    """List every camera permission granted to one user. Admin-only."""
    permissions = await permission_repository.list_for_user(user_id)
    return [
        CameraPermissionPublic(
            user_id=p.user_id,
            camera_name=p.camera_name,
            can_view=p.can_view,
            can_use_ptz=p.can_use_ptz,
        )
        for p in permissions
    ]
