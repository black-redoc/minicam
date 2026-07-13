"""Tests for app.dependencies: the DI wiring for the security layer.

Each dependency function is called directly with hand-built collaborators —
consistent with how the rest of the app tests services (see
test_authorization_service.py) — instead of going through FastAPI request
resolution, which is exercised end-to-end in tests/integration instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app import dependencies
from app.models.domain import Session, User
from app.services.auth import InvalidSessionTokenError, SessionTokenPayload
from app.services.network import NetworkZone

_USER = User(
    id=1,
    email="user@example.com",
    password_hash="hashed",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)
_ADMIN = User(
    id=2,
    email="admin@example.com",
    password_hash="hashed",
    is_admin=True,
    is_active=True,
    created_at="t",
    updated_at="t",
)
_SESSION = Session(
    jti="jti-1",
    user_id=1,
    issued_at="t",
    expires_at="t",
    revoked_at=None,
    user_agent=None,
    ip_address=None,
)


class TestGetCurrentUser:
    async def test_raises_401_when_no_cookie(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.get_current_user(
                None, MagicMock(), AsyncMock(), AsyncMock()
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_on_invalid_token(self) -> None:
        token_service = MagicMock()
        token_service.verify.side_effect = InvalidSessionTokenError("bad token")
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.get_current_user(
                "token", token_service, AsyncMock(), AsyncMock()
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_when_session_not_found(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        session_repository.get.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.get_current_user(
                "token", token_service, session_repository, AsyncMock()
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_when_session_revoked(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        session_repository.get.return_value = Session(
            jti="jti-1",
            user_id=1,
            issued_at="t",
            expires_at="t",
            revoked_at="t",
            user_agent=None,
            ip_address=None,
        )
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.get_current_user(
                "token", token_service, session_repository, AsyncMock()
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_when_user_missing(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        session_repository.get.return_value = _SESSION
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.get_current_user(
                "token", token_service, session_repository, user_repository
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_when_user_inactive(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        session_repository.get.return_value = _SESSION
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = User(
            id=1,
            email="x@example.com",
            password_hash="h",
            is_admin=False,
            is_active=False,
            created_at="t",
            updated_at="t",
        )
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.get_current_user(
                "token", token_service, session_repository, user_repository
            )
        assert exc_info.value.status_code == 401

    async def test_returns_user_on_valid_session(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        session_repository.get.return_value = _SESSION
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = _USER
        user = await dependencies.get_current_user(
            "token", token_service, session_repository, user_repository
        )
        assert user is _USER


class TestGetOptionalCurrentUser:
    async def test_returns_none_when_no_cookie(self) -> None:
        user = await dependencies.get_optional_current_user(
            None, MagicMock(), AsyncMock(), AsyncMock()
        )
        assert user is None

    async def test_returns_none_when_token_invalid(self) -> None:
        token_service = MagicMock()
        token_service.verify.side_effect = InvalidSessionTokenError("bad")
        user = await dependencies.get_optional_current_user(
            "token", token_service, AsyncMock(), AsyncMock()
        )
        assert user is None

    async def test_returns_user_when_session_valid(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        session_repository.get.return_value = _SESSION
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = _USER
        user = await dependencies.get_optional_current_user(
            "token", token_service, session_repository, user_repository
        )
        assert user is _USER


class TestRequireAdmin:
    async def test_raises_403_for_non_admin(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.require_admin(_USER)
        assert exc_info.value.status_code == 403

    async def test_returns_user_for_admin(self) -> None:
        assert await dependencies.require_admin(_ADMIN) is _ADMIN


class TestRequireCanView:
    async def test_raises_404_when_not_authorized(self) -> None:
        authorization_service = AsyncMock()
        authorization_service.can_view.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.require_can_view("cam1", _USER, authorization_service)
        assert exc_info.value.status_code == 404

    async def test_returns_user_when_authorized(self) -> None:
        authorization_service = AsyncMock()
        authorization_service.can_view.return_value = True
        user = await dependencies.require_can_view("cam1", _USER, authorization_service)
        assert user is _USER


class TestRequireCanUsePtz:
    async def test_raises_404_when_not_authorized(self) -> None:
        authorization_service = AsyncMock()
        authorization_service.can_use_ptz.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.require_can_use_ptz("cam1", _USER, authorization_service)
        assert exc_info.value.status_code == 404

    async def test_returns_user_when_authorized(self) -> None:
        authorization_service = AsyncMock()
        authorization_service.can_use_ptz.return_value = True
        user = await dependencies.require_can_use_ptz(
            "cam1", _USER, authorization_service
        )
        assert user is _USER


class TestEnforceWanAccessGate:
    async def test_lan_traffic_always_passes(self) -> None:
        wan_gate = AsyncMock()
        await dependencies.enforce_wan_access_gate(_USER, NetworkZone.LAN, wan_gate)
        wan_gate.is_active.assert_not_called()

    async def test_wan_traffic_passes_when_gate_active(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = True
        await dependencies.enforce_wan_access_gate(_USER, NetworkZone.WAN, wan_gate)

    async def test_wan_traffic_raises_403_when_gate_inactive(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.enforce_wan_access_gate(_USER, NetworkZone.WAN, wan_gate)
        assert exc_info.value.status_code == 403


class TestGetNetworkZone:
    def test_delegates_to_classify_network(self) -> None:
        request = MagicMock()
        request.client = MagicMock(host="192.168.1.5")
        assert dependencies.get_network_zone(request) is NetworkZone.LAN


class TestGetDbConnection:
    def test_delegates_to_connection_module(self) -> None:
        fake_conn = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(dependencies, "get_connection", lambda: fake_conn)
            assert dependencies.get_db_connection() is fake_conn


class TestRepositoryFactories:
    def test_get_user_repository_wraps_connection(self) -> None:
        conn = MagicMock()
        repo = dependencies.get_user_repository(conn)
        assert repo._conn is conn

    def test_get_session_repository_wraps_connection(self) -> None:
        conn = MagicMock()
        repo = dependencies.get_session_repository(conn)
        assert repo._conn is conn

    def test_get_permission_repository_wraps_connection(self) -> None:
        conn = MagicMock()
        repo = dependencies.get_permission_repository(conn)
        assert repo._conn is conn

    def test_get_wan_access_repository_wraps_connection(self) -> None:
        conn = MagicMock()
        repo = dependencies.get_wan_access_repository(conn)
        assert repo._conn is conn


class TestServiceFactories:
    def test_get_token_service_uses_settings_secret(self) -> None:
        settings = MagicMock(jwt_secret="s3cr3t", jwt_algorithm="HS256")
        service = dependencies.get_token_service(settings)
        assert service._secret == "s3cr3t"

    def test_get_stream_token_service_uses_settings_secret(self) -> None:
        settings = MagicMock(jwt_secret="s3cr3t", jwt_algorithm="HS256")
        service = dependencies.get_stream_token_service(settings)
        assert service._secret == "s3cr3t"

    def test_get_authorization_service_wraps_repository(self) -> None:
        repo = MagicMock()
        service = dependencies.get_authorization_service(repo)
        assert service._permission_repository is repo

    def test_get_wan_access_gate_service_wraps_repository(self) -> None:
        repo = MagicMock()
        service = dependencies.get_wan_access_gate_service(repo)
        assert service._wan_access_repository is repo
