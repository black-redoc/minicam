from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.dependencies import enforce_wan_access_gate, require_can_use_ptz
from app.models.domain import User
from app.routers import ptz as ptz_router

_AUTHORIZED_USER = User(
    id=1,
    email="ptz-user@example.com",
    password_hash="hashed",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)


@pytest.fixture
def mock_ptz() -> MagicMock:
    svc = MagicMock()
    svc.is_connected.return_value = True
    return svc


def _authorized_app() -> FastAPI:
    """A test app assuming an already-authorized caller — the authorization
    layer itself is exercised in tests/unit/test_dependencies.py and
    tests/integration.
    """
    test_app = FastAPI()
    test_app.include_router(ptz_router.router)
    test_app.dependency_overrides[require_can_use_ptz] = lambda: _AUTHORIZED_USER
    test_app.dependency_overrides[enforce_wan_access_gate] = lambda: None
    return test_app


@pytest.fixture
def client(mock_ptz: MagicMock) -> TestClient:
    state_module.ptz_services["cam1"] = mock_ptz
    yield TestClient(_authorized_app())
    state_module.ptz_services.clear()


class TestPtzEndpointValidation:
    def test_unknown_action_returns_400(self, client: TestClient) -> None:
        res = client.post("/api/ptz/cam1/zoom_in")
        assert res.status_code == 400

    def test_unknown_action_message(self, client: TestClient) -> None:
        res = client.post("/api/ptz/cam1/fly")
        assert "fly" in res.json()["detail"]


class TestPtzEndpointSuccess:
    @pytest.mark.parametrize("action", ["up", "down", "left", "right", "stop"])
    def test_valid_action_returns_200(self, client: TestClient, action: str) -> None:
        res = client.post(f"/api/ptz/cam1/{action}")
        assert res.status_code == 200

    @pytest.mark.parametrize("action", ["up", "down", "left", "right", "stop"])
    def test_response_contains_action(self, client: TestClient, action: str) -> None:
        res = client.post(f"/api/ptz/cam1/{action}")
        assert res.json()["action"] == action
        assert res.json()["status"] == "ok"

    @pytest.mark.parametrize("action", ["up", "down", "left", "right", "stop"])
    def test_calls_correct_service_method(
        self, client: TestClient, mock_ptz: MagicMock, action: str
    ) -> None:
        client.post(f"/api/ptz/cam1/{action}")
        mock_ptz.run_command.assert_called_once_with(action)


class TestPtzEndpointDelegatesToService:
    def test_delegates_connect_and_action_to_run_command(
        self, client: TestClient, mock_ptz: MagicMock
    ) -> None:
        # Connect-if-needed + the action itself are one atomic call inside
        # PTZService (see test_ptz.py) so concurrent requests can't interleave
        # ONVIF calls on the same camera; the router just delegates.
        client.post("/api/ptz/cam1/up")
        mock_ptz.run_command.assert_called_once_with("up")


class TestPtzEndpointErrors:
    def test_returns_503_when_camera_not_found(self) -> None:
        state_module.ptz_services.clear()
        client = TestClient(_authorized_app())
        res = client.post("/api/ptz/nonexistent/up")
        assert res.status_code == 503

    def test_returns_503_on_service_exception(self) -> None:
        svc = MagicMock()
        svc.run_command.side_effect = RuntimeError("ONVIF timeout")
        state_module.ptz_services["err_cam"] = svc
        client = TestClient(_authorized_app())
        res = client.post("/api/ptz/err_cam/up")
        assert res.status_code == 503
        assert "ONVIF timeout" in res.json()["detail"]
        state_module.ptz_services.pop("err_cam", None)

    def test_reconnects_and_retries_on_connection_reset(self) -> None:
        svc = MagicMock()
        svc.run_command.side_effect = Exception(
            "Unknown error: ('Connection aborted.', "
            "ConnectionResetError(104, 'Connection reset by peer'))"
        )
        svc.reconnect_and_run.return_value = None
        state_module.ptz_services["retry_cam"] = svc
        client = TestClient(_authorized_app())
        res = client.post("/api/ptz/retry_cam/up")
        assert res.status_code == 200
        svc.run_command.assert_called_once_with("up")
        svc.reconnect_and_run.assert_called_once_with("up")
        state_module.ptz_services.pop("retry_cam", None)

    def test_returns_503_if_retry_also_fails_on_connection_error(self) -> None:
        svc = MagicMock()
        svc.run_command.side_effect = Exception("Connection reset by peer")
        svc.reconnect_and_run.side_effect = Exception("Connection reset by peer")
        state_module.ptz_services["fail_cam"] = svc
        client = TestClient(_authorized_app())
        res = client.post("/api/ptz/fail_cam/up")
        assert res.status_code == 503
        state_module.ptz_services.pop("fail_cam", None)
