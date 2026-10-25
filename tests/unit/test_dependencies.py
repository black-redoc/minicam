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
from app.security_settings import SecuritySettings
from app.services.auth import InvalidSessionTokenError, SessionTokenPayload
from app.services.cloudflare_access import (
    AccessTokenPayload,
    InvalidAccessTokenError,
)
from app.services.network import NetworkZone

_CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"

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


class TestGetSessionJti:
    def test_raises_401_when_no_cookie(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            dependencies.get_session_jti(None, MagicMock())
        assert exc_info.value.status_code == 401

    def test_raises_401_on_invalid_token(self) -> None:
        token_service = MagicMock()
        token_service.verify.side_effect = InvalidSessionTokenError("bad token")
        with pytest.raises(HTTPException) as exc_info:
            dependencies.get_session_jti("token", token_service)
        assert exc_info.value.status_code == 401

    def test_returns_the_session_jti(self) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        assert dependencies.get_session_jti("token", token_service) == "jti-1"


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


def _request_with_access_header(token: str | None) -> MagicMock:
    request = MagicMock()
    request.headers = {_CF_ACCESS_JWT_HEADER: token} if token is not None else {}
    return request


class TestEnforceWanAccessGate:
    async def test_lan_traffic_always_passes(self) -> None:
        wan_gate = AsyncMock()
        request = _request_with_access_header(None)
        await dependencies.enforce_wan_access_gate(
            request, _USER, NetworkZone.LAN, wan_gate, MagicMock()
        )
        wan_gate.is_active.assert_not_called()

    async def test_wan_traffic_raises_403_when_gate_inactive(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = False
        request = _request_with_access_header(None)
        with pytest.raises(HTTPException) as exc_info:
            await dependencies.enforce_wan_access_gate(
                request, _USER, NetworkZone.WAN, wan_gate, MagicMock()
            )
        assert exc_info.value.status_code == 403

    async def test_wan_traffic_raises_403_when_verifier_not_configured(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = True
        request = _request_with_access_header(None)

        with pytest.raises(HTTPException) as exc_info:
            await dependencies.enforce_wan_access_gate(
                request, _USER, NetworkZone.WAN, wan_gate, None
            )
        assert exc_info.value.status_code == 403

    async def test_wan_traffic_raises_403_when_access_header_missing(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = True
        verifier = MagicMock()
        request = _request_with_access_header(None)

        with pytest.raises(HTTPException) as exc_info:
            await dependencies.enforce_wan_access_gate(
                request, _USER, NetworkZone.WAN, wan_gate, verifier
            )
        assert exc_info.value.status_code == 403
        verifier.verify.assert_not_called()

    async def test_wan_traffic_raises_403_when_access_token_invalid(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = True
        verifier = MagicMock()
        verifier.verify.side_effect = InvalidAccessTokenError("bad token")
        request = _request_with_access_header("token")

        with pytest.raises(HTTPException) as exc_info:
            await dependencies.enforce_wan_access_gate(
                request, _USER, NetworkZone.WAN, wan_gate, verifier
            )
        assert exc_info.value.status_code == 403

    async def test_wan_traffic_raises_403_when_access_email_does_not_match_session(
        self,
    ) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = True
        verifier = MagicMock()
        verifier.verify.return_value = AccessTokenPayload(
            email="someone-else@example.com"
        )
        request = _request_with_access_header("token")

        with pytest.raises(HTTPException) as exc_info:
            await dependencies.enforce_wan_access_gate(
                request, _USER, NetworkZone.WAN, wan_gate, verifier
            )
        assert exc_info.value.status_code == 403

    async def test_wan_traffic_passes_when_access_email_matches_session(self) -> None:
        wan_gate = AsyncMock()
        wan_gate.is_active.return_value = True
        verifier = MagicMock()
        verifier.verify.return_value = AccessTokenPayload(email=_USER.email)
        request = _request_with_access_header("token")

        await dependencies.enforce_wan_access_gate(
            request, _USER, NetworkZone.WAN, wan_gate, verifier
        )


class TestGetCloudflareAccessVerifier:
    def test_returns_none_when_team_domain_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = SecuritySettings(
            jwt_secret="s",
            cf_access_team_domain=None,
            cf_access_aud="aud-tag",
        )
        monkeypatch.setattr(dependencies, "get_security_settings", lambda: settings)
        dependencies.get_cloudflare_access_verifier.cache_clear()

        assert dependencies.get_cloudflare_access_verifier() is None
        dependencies.get_cloudflare_access_verifier.cache_clear()

    def test_returns_none_when_aud_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = SecuritySettings(
            jwt_secret="s",
            cf_access_team_domain="https://myteam.cloudflareaccess.com",
            cf_access_aud=None,
        )
        monkeypatch.setattr(dependencies, "get_security_settings", lambda: settings)
        dependencies.get_cloudflare_access_verifier.cache_clear()

        assert dependencies.get_cloudflare_access_verifier() is None
        dependencies.get_cloudflare_access_verifier.cache_clear()

    def test_builds_verifier_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = SecuritySettings(
            jwt_secret="s",
            cf_access_team_domain="https://myteam.cloudflareaccess.com",
            cf_access_aud="aud-tag",
        )
        monkeypatch.setattr(dependencies, "get_security_settings", lambda: settings)
        dependencies.get_cloudflare_access_verifier.cache_clear()

        verifier = dependencies.get_cloudflare_access_verifier()

        assert verifier is not None
        assert verifier._audience == "aud-tag"
        assert verifier._issuer == "https://myteam.cloudflareaccess.com"
        dependencies.get_cloudflare_access_verifier.cache_clear()


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
        used = dependencies.get_used_stream_tokens()
        service = dependencies.get_stream_token_service(settings, used)
        assert service._secret == "s3cr3t"
        assert service._used_tokens is used

    def test_used_stream_tokens_is_a_singleton(self) -> None:
        assert (
            dependencies.get_used_stream_tokens()
            is dependencies.get_used_stream_tokens()
        )

    def test_get_authorization_service_wraps_repository(self) -> None:
        repo = MagicMock()
        service = dependencies.get_authorization_service(repo)
        assert service._permission_repository is repo

    def test_get_wan_access_gate_service_wraps_repository(self) -> None:
        repo = MagicMock()
        service = dependencies.get_wan_access_gate_service(repo)
        assert service._wan_access_repository is repo

    def test_get_stream_access_service_wraps_its_collaborators(self) -> None:
        sessions, users, authz, gate = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        service = dependencies.get_stream_access_service(sessions, users, authz, gate)
        assert service._session_repository is sessions
        assert service._user_repository is users
        assert service._authorization_service is authz
        assert service._wan_access_gate is gate
