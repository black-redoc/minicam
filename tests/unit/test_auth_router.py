"""Router-level tests for /api/auth/*, using dependency_overrides for every
collaborator — business logic itself is covered by the service/repository
unit tests; this file only checks wiring, status codes, and cookie handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import (
    get_current_user,
    get_db_connection,
    get_login_rate_limiter,
    get_network_zone,
    get_password_hasher,
    get_permission_repository,
    get_security_settings,
    get_session_repository,
    get_token_service,
    get_user_repository,
    require_admin,
)
from app.models.domain import CameraPermission, User
from app.routers import auth as auth_router
from app.security_settings import SecuritySettings
from app.services.auth import InvalidSessionTokenError, SessionTokenPayload
from app.services.login_rate_limiter import LoginRateLimiter
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
_SETTINGS = SecuritySettings(jwt_secret="test-secret")


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(auth_router.router)
    # Repositories not explicitly overridden by a test still get constructed
    # (FastAPI resolves every declared dependency up front); give them an
    # inert connection so unrelated repos never hit the real DB singleton.
    test_app.dependency_overrides[get_db_connection] = lambda: MagicMock()
    test_app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
    return test_app


class TestLogin:
    def test_returns_401_on_unknown_email(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = None
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": "ghost@example.com", "password": "x"}
        )
        assert res.status_code == 401

    def test_returns_401_on_wrong_password(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = _USER
        password_hasher = MagicMock()
        password_hasher.verify.return_value = False
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_password_hasher] = lambda: password_hasher
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": _USER.email, "password": "wrong"}
        )
        assert res.status_code == 401

    def test_returns_401_when_user_inactive(self, app: FastAPI) -> None:
        inactive = User(
            id=1,
            email="inactive@example.com",
            password_hash="h",
            is_admin=False,
            is_active=False,
            created_at="t",
            updated_at="t",
        )
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = inactive
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": inactive.email, "password": "x"}
        )
        assert res.status_code == 401

    def test_sets_session_cookie_on_success(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = _USER
        password_hasher = MagicMock()
        password_hasher.verify.return_value = True
        session_repository = AsyncMock()
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_password_hasher] = lambda: password_hasher
        app.dependency_overrides[get_session_repository] = lambda: session_repository
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": _USER.email, "password": "correct"}
        )
        assert res.status_code == 200
        assert "minicam_session" in res.cookies
        session_repository.create.assert_called_once()

    def test_response_body_never_leaks_password_hash(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = _USER
        password_hasher = MagicMock()
        password_hasher.verify.return_value = True
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_password_hasher] = lambda: password_hasher
        app.dependency_overrides[get_session_repository] = lambda: AsyncMock()
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": _USER.email, "password": "correct"}
        )
        assert "password_hash" not in res.json()

    def test_cookie_is_not_secure_on_lan(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = _USER
        password_hasher = MagicMock()
        password_hasher.verify.return_value = True
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_password_hasher] = lambda: password_hasher
        app.dependency_overrides[get_session_repository] = lambda: AsyncMock()
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        # get_network_zone already defaults to LAN in the app fixture.
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": _USER.email, "password": "correct"}
        )
        assert res.status_code == 200
        assert "secure" not in res.headers["set-cookie"].lower()

    def test_cookie_is_secure_on_wan(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = _USER
        password_hasher = MagicMock()
        password_hasher.verify.return_value = True
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_password_hasher] = lambda: password_hasher
        app.dependency_overrides[get_session_repository] = lambda: AsyncMock()
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        client = TestClient(app)

        res = client.post(
            "/api/auth/login", json={"email": _USER.email, "password": "correct"}
        )
        assert res.status_code == 200
        assert "secure" in res.headers["set-cookie"].lower()


class TestLoginRateLimiting:
    def test_returns_429_once_the_limit_is_exceeded(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = None
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        # Same shared instance on every resolution — a fresh lambda would
        # hand back a brand new (and thus always-empty) limiter per request.
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=900)
        app.dependency_overrides[get_login_rate_limiter] = lambda: limiter
        client = TestClient(app)
        payload = {"email": "attacker@example.com", "password": "wrong"}

        first = client.post("/api/auth/login", json=payload)
        second = client.post("/api/auth/login", json=payload)
        third = client.post("/api/auth/login", json=payload)

        assert first.status_code == 401
        assert second.status_code == 401
        assert third.status_code == 429

    def test_does_not_query_the_database_once_the_limit_is_exceeded(
        self, app: FastAPI
    ) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = None
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        limiter = LoginRateLimiter(max_attempts=1, window_seconds=900)
        app.dependency_overrides[get_login_rate_limiter] = lambda: limiter
        client = TestClient(app)
        payload = {"email": "attacker@example.com", "password": "wrong"}

        client.post("/api/auth/login", json=payload)
        user_repository.get_by_email.reset_mock()
        res = client.post("/api/auth/login", json=payload)

        assert res.status_code == 429
        user_repository.get_by_email.assert_not_called()


class TestLogout:
    def test_revokes_session_and_clears_cookie(self, app: FastAPI) -> None:
        token_service = MagicMock()
        token_service.verify.return_value = SessionTokenPayload(user_id=1, jti="jti-1")
        session_repository = AsyncMock()
        app.dependency_overrides[get_token_service] = lambda: token_service
        app.dependency_overrides[get_session_repository] = lambda: session_repository
        client = TestClient(app)
        client.cookies.set("minicam_session", "some-token")

        res = client.post("/api/auth/logout")
        assert res.status_code == 200
        session_repository.revoke.assert_called_once_with("jti-1")

    def test_is_idempotent_with_no_cookie(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.post("/api/auth/logout")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_swallows_invalid_token_without_erroring(self, app: FastAPI) -> None:
        token_service = MagicMock()
        token_service.verify.side_effect = InvalidSessionTokenError("bad")
        session_repository = AsyncMock()
        app.dependency_overrides[get_token_service] = lambda: token_service
        app.dependency_overrides[get_session_repository] = lambda: session_repository
        client = TestClient(app)
        client.cookies.set("minicam_session", "garbage")

        res = client.post("/api/auth/logout")
        assert res.status_code == 200
        session_repository.revoke.assert_not_called()


class TestMe:
    def test_returns_current_user(self, app: FastAPI) -> None:
        app.dependency_overrides[get_current_user] = lambda: _USER
        client = TestClient(app)

        res = client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json() == {
            "id": _USER.id,
            "email": _USER.email,
            "is_admin": _USER.is_admin,
        }


class TestCreateUser:
    def test_requires_admin(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.post(
            "/api/auth/users",
            json={"email": "new@example.com", "password": "longenough"},
        )
        assert res.status_code == 401  # require_admin -> get_current_user -> 401

    def test_returns_409_when_email_taken(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = _USER
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.post(
            "/api/auth/users",
            json={"email": _USER.email, "password": "longenough"},
        )
        assert res.status_code == 409

    def test_returns_201_on_success(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = None
        user_repository.create.return_value = _USER
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.post(
            "/api/auth/users",
            json={"email": "new@example.com", "password": "longenough"},
        )
        assert res.status_code == 201


class TestListUsers:
    def test_requires_admin(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.get("/api/auth/users")
        assert res.status_code == 401

    def test_returns_public_projection_of_every_user(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.list_all.return_value = [_USER, _ADMIN]
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.get("/api/auth/users")
        assert res.status_code == 200
        assert res.json() == [
            {"id": _USER.id, "email": _USER.email, "is_admin": _USER.is_admin},
            {"id": _ADMIN.id, "email": _ADMIN.email, "is_admin": _ADMIN.is_admin},
        ]
        assert "password_hash" not in res.text


class TestDeleteUser:
    def test_requires_admin(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.delete("/api/auth/users/1")
        assert res.status_code == 401

    def test_returns_400_when_deleting_self(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.delete(f"/api/auth/users/{_ADMIN.id}")
        assert res.status_code == 400
        user_repository.delete.assert_not_called()

    def test_returns_404_when_user_not_found(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = None
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.delete("/api/auth/users/999")
        assert res.status_code == 404
        user_repository.delete.assert_not_called()

    def test_returns_409_when_deleting_the_last_admin(self, app: FastAPI) -> None:
        other_admin = User(
            id=3,
            email="other-admin@example.com",
            password_hash="hashed",
            is_admin=True,
            is_active=True,
            created_at="t",
            updated_at="t",
        )
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = other_admin
        user_repository.count_admins.return_value = 1
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.delete(f"/api/auth/users/{other_admin.id}")
        assert res.status_code == 409
        user_repository.delete.assert_not_called()

    def test_returns_204_when_deleting_another_admin_and_admins_remain(
        self, app: FastAPI
    ) -> None:
        other_admin = User(
            id=3,
            email="other-admin@example.com",
            password_hash="hashed",
            is_admin=True,
            is_active=True,
            created_at="t",
            updated_at="t",
        )
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = other_admin
        user_repository.count_admins.return_value = 2
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.delete(f"/api/auth/users/{other_admin.id}")
        assert res.status_code == 204
        user_repository.delete.assert_called_once_with(other_admin.id)

    def test_returns_204_on_success_for_non_admin_target(self, app: FastAPI) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_id.return_value = _USER
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.delete(f"/api/auth/users/{_USER.id}")
        assert res.status_code == 204
        user_repository.count_admins.assert_not_called()
        user_repository.delete.assert_called_once_with(_USER.id)


class TestSetPermission:
    def test_requires_admin(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.post(
            "/api/auth/permissions",
            json={"user_id": 1, "camera_name": "cam1", "can_view": True},
        )
        assert res.status_code == 401

    def test_returns_200_on_success(self, app: FastAPI) -> None:
        permission_repository = AsyncMock()
        permission_repository.upsert.return_value = CameraPermission(
            id=1,
            user_id=1,
            camera_name="cam1",
            can_view=True,
            can_use_ptz=False,
            created_at="t",
        )
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_permission_repository] = lambda: (
            permission_repository
        )
        client = TestClient(app)

        res = client.post(
            "/api/auth/permissions",
            json={"user_id": 1, "camera_name": "cam1", "can_view": True},
        )
        assert res.status_code == 200
        assert res.json()["can_view"] is True


class TestListPermissions:
    def test_requires_admin(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.get("/api/auth/permissions", params={"user_id": 1})
        assert res.status_code == 401

    def test_returns_permissions_for_the_requested_user(self, app: FastAPI) -> None:
        permission_repository = AsyncMock()
        permission_repository.list_for_user.return_value = [
            CameraPermission(1, 1, "cam1", True, False, "t"),
            CameraPermission(2, 1, "cam2", False, True, "t"),
        ]
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_permission_repository] = lambda: (
            permission_repository
        )
        client = TestClient(app)

        res = client.get("/api/auth/permissions", params={"user_id": 1})
        assert res.status_code == 200
        assert res.json() == [
            {
                "user_id": 1,
                "camera_name": "cam1",
                "can_view": True,
                "can_use_ptz": False,
            },
            {
                "user_id": 1,
                "camera_name": "cam2",
                "can_view": False,
                "can_use_ptz": True,
            },
        ]
        permission_repository.list_for_user.assert_called_once_with(1)


class TestSetupStatus:
    @pytest.mark.parametrize(("count", "expected"), [(0, True), (1, False)])
    def test_reports_whether_setup_is_required(
        self, app: FastAPI, count: int, expected: bool
    ) -> None:
        user_repository = AsyncMock()
        user_repository.count_users.return_value = count
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        client = TestClient(app)

        res = client.get("/api/auth/setup")
        assert res.status_code == 200
        assert res.json() == {"setup_required": expected}


class TestCompleteSetup:
    def _wire(self, app: FastAPI, created: User | None) -> AsyncMock:
        user_repository = AsyncMock()
        user_repository.create_first_admin.return_value = created
        password_hasher = MagicMock()
        password_hasher.hash.return_value = "hashed"
        token_service = MagicMock()
        token_service.issue.return_value = "signed-token"
        app.dependency_overrides[get_user_repository] = lambda: user_repository
        app.dependency_overrides[get_password_hasher] = lambda: password_hasher
        app.dependency_overrides[get_session_repository] = lambda: AsyncMock()
        app.dependency_overrides[get_token_service] = lambda: token_service
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        return user_repository

    def test_creates_admin_and_sets_session_cookie(self, app: FastAPI) -> None:
        user_repository = self._wire(app, _ADMIN)
        client = TestClient(app)

        res = client.post(
            "/api/auth/setup",
            json={"email": _ADMIN.email, "password": "long-enough"},
        )
        assert res.status_code == 201
        assert res.json() == {"id": 2, "email": _ADMIN.email, "is_admin": True}
        assert res.cookies.get("minicam_session") == "signed-token"
        user_repository.create_first_admin.assert_awaited_once_with(
            _ADMIN.email, "hashed"
        )

    def test_returns_409_when_a_user_already_exists(self, app: FastAPI) -> None:
        self._wire(app, None)
        client = TestClient(app)

        res = client.post(
            "/api/auth/setup",
            json={"email": "late@example.com", "password": "long-enough"},
        )
        assert res.status_code == 409
        assert "minicam_session" not in res.cookies

    def test_returns_403_from_wan(self, app: FastAPI) -> None:
        user_repository = self._wire(app, _ADMIN)
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        client = TestClient(app)

        res = client.post(
            "/api/auth/setup",
            json={"email": _ADMIN.email, "password": "long-enough"},
        )
        assert res.status_code == 403
        user_repository.create_first_admin.assert_not_awaited()

    def test_rejects_short_password(self, app: FastAPI) -> None:
        user_repository = self._wire(app, _ADMIN)
        client = TestClient(app)

        res = client.post(
            "/api/auth/setup", json={"email": _ADMIN.email, "password": "short"}
        )
        assert res.status_code == 422
        user_repository.create_first_admin.assert_not_awaited()
