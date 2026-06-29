from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.routers import ptz as ptz_router


@pytest.fixture
def mock_ptz() -> MagicMock:
    svc = MagicMock()
    svc.is_connected.return_value = True
    return svc


@pytest.fixture
def client(mock_ptz: MagicMock, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(state_module, "ptz", mock_ptz)
    test_app = FastAPI()
    test_app.include_router(ptz_router.router)
    return TestClient(test_app)


class TestPtzEndpointValidation:
    def test_unknown_action_returns_400(self, client: TestClient) -> None:
        res = client.post("/api/ptz/zoom_in")
        assert res.status_code == 400

    def test_unknown_action_message(self, client: TestClient) -> None:
        res = client.post("/api/ptz/fly")
        assert "fly" in res.json()["detail"]


class TestPtzEndpointSuccess:
    @pytest.mark.parametrize("action", ["up", "down", "left", "right", "stop"])
    def test_valid_action_returns_200(self, client: TestClient, action: str) -> None:
        res = client.post(f"/api/ptz/{action}")
        assert res.status_code == 200

    @pytest.mark.parametrize("action", ["up", "down", "left", "right", "stop"])
    def test_response_contains_action(self, client: TestClient, action: str) -> None:
        res = client.post(f"/api/ptz/{action}")
        assert res.json()["action"] == action
        assert res.json()["status"] == "ok"

    @pytest.mark.parametrize("action", ["up", "down", "left", "right", "stop"])
    def test_calls_correct_service_method(
        self, client: TestClient, mock_ptz: MagicMock, action: str
    ) -> None:
        client.post(f"/api/ptz/{action}")
        getattr(mock_ptz, action).assert_called_once()


class TestPtzEndpointLazyConnect:
    def test_connects_when_not_connected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = MagicMock()
        svc.is_connected.return_value = False
        monkeypatch.setattr(state_module, "ptz", svc)

        test_app = FastAPI()
        test_app.include_router(ptz_router.router)
        client = TestClient(test_app)

        client.post("/api/ptz/stop")
        svc.connect.assert_called_once()

    def test_does_not_reconnect_when_already_connected(
        self, client: TestClient, mock_ptz: MagicMock
    ) -> None:
        client.post("/api/ptz/up")
        mock_ptz.connect.assert_not_called()


class TestPtzEndpointErrors:
    def test_returns_503_when_ptz_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(state_module, "ptz", None)
        test_app = FastAPI()
        test_app.include_router(ptz_router.router)
        client = TestClient(test_app)

        res = client.post("/api/ptz/up")
        assert res.status_code == 503

    def test_returns_503_on_service_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = MagicMock()
        svc.is_connected.return_value = True
        svc.up.side_effect = RuntimeError("ONVIF timeout")
        monkeypatch.setattr(state_module, "ptz", svc)

        test_app = FastAPI()
        test_app.include_router(ptz_router.router)
        client = TestClient(test_app)

        res = client.post("/api/ptz/up")
        assert res.status_code == 503
        assert "ONVIF timeout" in res.json()["detail"]

    def test_reconnects_and_retries_on_connection_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = MagicMock()
        svc.is_connected.return_value = True
        svc.up.side_effect = [
            Exception("Unknown error: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))"),
            None,
        ]
        monkeypatch.setattr(state_module, "ptz", svc)

        test_app = FastAPI()
        test_app.include_router(ptz_router.router)
        client = TestClient(test_app)

        res = client.post("/api/ptz/up")
        assert res.status_code == 200
        svc.disconnect.assert_called_once()
        svc.connect.assert_called_once()
        assert svc.up.call_count == 2

    def test_returns_503_if_retry_also_fails_on_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = MagicMock()
        svc.is_connected.return_value = True
        svc.up.side_effect = Exception("Connection reset by peer")
        monkeypatch.setattr(state_module, "ptz", svc)

        test_app = FastAPI()
        test_app.include_router(ptz_router.router)
        client = TestClient(test_app)

        res = client.post("/api/ptz/up")
        assert res.status_code == 503
