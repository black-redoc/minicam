"""Integration: the endpoints used by the native mobile clients — the JSON
camera list and the Cloudflare Access token handoff — backed by a real
SQLite database.

As in test_wan_access_gate_flow.py, the WAN zone and the Access verifier are
overridden instead of faking client IPs or signing real Access JWTs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_cloudflare_access_verifier, get_network_zone
from app.models.config import AppConfig, CameraConfig
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import mobile as mobile_router
from app.services.auth import PasswordHasher
from app.services.cloudflare_access import AccessTokenPayload, InvalidAccessTokenError
from app.services.network import NetworkZone

_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
_STATE = "s" * 43


def _verifier(email: str = "remote@example.com") -> MagicMock:
    verifier = MagicMock()
    verifier.verify.return_value = AccessTokenPayload(email=email)
    return verifier


class TestMyCameras:
    async def test_requires_session(self, client: TestClient) -> None:
        assert client.get("/api/me/cameras").status_code == 401

    async def test_lists_only_viewable_cameras_in_config_order(
        self, client: TestClient, db_connection: aiosqlite.Connection
    ) -> None:
        user = await SQLiteUserRepository(db_connection).create(
            "viewer@example.com", PasswordHasher().hash("pw"), is_admin=False
        )
        perms = SQLitePermissionRepository(db_connection)
        await perms.upsert(
            user.id, "patio", can_view=True, can_use_ptz=True, can_listen=True
        )
        await perms.upsert(user.id, "entrada", can_view=True, can_use_ptz=False)
        # PTZ-only grant: not viewable, so not listed.
        await perms.upsert(user.id, "garaje", can_view=False, can_use_ptz=True)
        # Permission for a camera that is no longer configured: not listed.
        await perms.upsert(user.id, "removed", can_view=True, can_use_ptz=False)
        client.post(
            "/api/auth/login", json={"email": "viewer@example.com", "password": "pw"}
        )

        config = AppConfig(
            cameras=[
                CameraConfig(name="entrada", host="192.168.1.10"),
                CameraConfig(name="garaje", host="192.168.1.11"),
                CameraConfig(name="patio", host="192.168.1.12"),
            ]
        )
        with patch.object(mobile_router, "get_config", return_value=config):
            res = client.get("/api/me/cameras")

        assert res.status_code == 200
        assert res.json() == [
            {"name": "entrada", "can_use_ptz": False, "can_listen": False},
            {"name": "patio", "can_use_ptz": True, "can_listen": True},
        ]


class TestAccessHandoff:
    def test_redirects_to_app_with_token_in_fragment(
        self, app: FastAPI, client: TestClient
    ) -> None:
        verifier = _verifier()
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: verifier

        res = client.get(
            f"/api/mobile/access-handoff?state={_STATE}",
            headers={_ACCESS_JWT_HEADER: "eyJ.abc.def"},
            follow_redirects=False,
        )

        assert res.status_code == 302
        assert (
            res.headers["location"]
            == f"minicam://access-callback?state={_STATE}#token=eyJ.abc.def"
        )
        assert res.headers["cache-control"] == "no-store"
        verifier.verify.assert_called_once_with("eyJ.abc.def")

    def test_rejects_malformed_state(self, app: FastAPI, client: TestClient) -> None:
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: _verifier()

        for state in ["short", "x" * 129, "a" * 40 + "&evil=1", "a" * 40 + "#"]:
            res = client.get(
                "/api/mobile/access-handoff",
                params={"state": state},
                headers={_ACCESS_JWT_HEADER: "eyJ.abc.def"},
                follow_redirects=False,
            )
            assert res.status_code == 400, state

    def test_refuses_on_lan(self, client: TestClient) -> None:
        res = client.get(
            f"/api/mobile/access-handoff?state={_STATE}",
            headers={_ACCESS_JWT_HEADER: "eyJ.abc.def"},
            follow_redirects=False,
        )
        assert res.status_code == 403

    def test_refuses_when_access_not_configured(
        self, app: FastAPI, client: TestClient
    ) -> None:
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: None

        res = client.get(
            f"/api/mobile/access-handoff?state={_STATE}",
            headers={_ACCESS_JWT_HEADER: "eyJ.abc.def"},
            follow_redirects=False,
        )
        assert res.status_code == 403

    def test_refuses_without_access_header(
        self, app: FastAPI, client: TestClient
    ) -> None:
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: _verifier()

        res = client.get(
            f"/api/mobile/access-handoff?state={_STATE}", follow_redirects=False
        )
        assert res.status_code == 403

    def test_refuses_invalid_access_token(
        self, app: FastAPI, client: TestClient
    ) -> None:
        verifier = MagicMock()
        verifier.verify.side_effect = InvalidAccessTokenError("forged")
        app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
        app.dependency_overrides[get_cloudflare_access_verifier] = lambda: verifier

        res = client.get(
            f"/api/mobile/access-handoff?state={_STATE}",
            headers={_ACCESS_JWT_HEADER: "forged"},
            follow_redirects=False,
        )
        assert res.status_code == 403
        assert "location" not in res.headers
