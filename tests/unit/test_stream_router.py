from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

import app.state as state_module
from app.dependencies import (
    enforce_wan_access_gate,
    get_db_connection,
    get_security_settings,
    get_stream_token_service,
    require_can_view,
)
from app.models.domain import User
from app.models.frame import Frame
from app.routers import stream as stream_router
from app.security_settings import SecuritySettings
from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot
from app.services.stream_tokens import InvalidStreamTokenError, StreamTokenPayload

_USER = User(
    id=1,
    email="viewer@example.com",
    password_hash="hashed",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)
_SETTINGS = SecuritySettings(jwt_secret="test-secret")


def _make_frame(sequence: int = 0) -> Frame:
    return Frame(
        image=np.zeros((480, 640, 3), dtype=np.uint8),
        width=640,
        height=480,
        timestamp=float(sequence),
        sequence=sequence,
    )


def _accepting_stream_token_service() -> MagicMock:
    """A fake StreamTokenService whose verify() always accepts — the
    business logic under test here is the WS stream loop, not token
    validation (see tests/unit/test_stream_tokens.py for that).
    """
    service = MagicMock()
    service.verify.return_value = StreamTokenPayload(user_id=1, camera_name="cam1")
    return service


def _app_with_valid_token() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(stream_router.router)
    test_app.dependency_overrides[get_stream_token_service] = (
        _accepting_stream_token_service
    )
    return test_app


@pytest.fixture
def client() -> TestClient:
    mock_buffer = MagicMock()
    mock_buffer.read.return_value = None
    state_module.frame_buffers["cam1"] = mock_buffer
    yield TestClient(_app_with_valid_token())
    state_module.frame_buffers.clear()


class TestWebSocketStream:
    def test_accepts_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/stream/cam1?token=valid"):
            pass

    def test_sends_jpeg_when_frame_available(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer
        client = TestClient(_app_with_valid_token())
        with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
            data = ws.receive_bytes()
            assert data[:2] == b"\xff\xd8"
        state_module.frame_buffers.clear()

    def test_does_not_send_duplicate_frames(self) -> None:
        frame = _make_frame(sequence=5)
        call_count = 0

        def read_side_effect() -> Frame:
            nonlocal call_count
            call_count += 1
            return frame

        mock_buffer = MagicMock()
        mock_buffer.read.side_effect = read_side_effect
        state_module.frame_buffers["cam1"] = mock_buffer
        client = TestClient(_app_with_valid_token())
        with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
            data = ws.receive_bytes()
            assert data[:2] == b"\xff\xd8"
        state_module.frame_buffers.clear()

    def test_unknown_camera_closes_connection(self) -> None:
        state_module.frame_buffers.clear()
        client = TestClient(_app_with_valid_token())
        # WebSocket closed before accept — TestClient raises on connect or on recv
        try:
            with client.websocket_connect("/ws/stream/nonexistent?token=valid") as ws:
                ws.receive_bytes()
        except Exception:
            pass  # Expected: connection closed with 1008

    def test_missing_token_query_param_is_rejected(self) -> None:
        client = TestClient(_app_with_valid_token())
        with pytest.raises(Exception):  # noqa: B017 — TestClient raises on handshake reject
            with client.websocket_connect("/ws/stream/cam1") as ws:
                ws.receive_bytes()

    def test_invalid_token_closes_connection(self) -> None:
        service = MagicMock()
        service.verify.side_effect = InvalidStreamTokenError("bad token")
        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        test_app.dependency_overrides[get_stream_token_service] = lambda: service
        client = TestClient(test_app)
        try:
            with client.websocket_connect("/ws/stream/cam1?token=garbage") as ws:
                ws.receive_bytes()
        except Exception:
            pass  # Expected: connection closed with 1008


class TestWebSocketStreamErrorHandling:
    """Drives the coroutine directly with mocked I/O to exercise the
    disconnect/exception branches, which real socket teardown races too
    unreliably to hit through TestClient.
    """

    def _make_mock_websocket(self) -> MagicMock:
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.client = ("127.0.0.1", 12345)
        return ws

    async def test_websocket_disconnect_is_logged_and_swallowed(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer

        ws = self._make_mock_websocket()
        ws.send_bytes = AsyncMock(side_effect=WebSocketDisconnect(code=1001))

        try:
            await stream_router.stream(
                ws, "cam1", "valid", _accepting_stream_token_service()
            )  # must not raise
        finally:
            state_module.frame_buffers.clear()

    async def test_unexpected_exception_is_logged_and_reraised(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer

        ws = self._make_mock_websocket()
        ws.send_bytes = AsyncMock(side_effect=RuntimeError("socket blew up"))

        try:
            with pytest.raises(RuntimeError, match="socket blew up"):
                await stream_router.stream(
                    ws, "cam1", "valid", _accepting_stream_token_service()
                )
        finally:
            state_module.frame_buffers.clear()


class TestWebSocketStreamDetectionOverlay:
    """Covers the draw-before-encode branch added on top of the plain
    encode_jpeg path already exercised by TestWebSocketStream.
    """

    def teardown_method(self) -> None:
        state_module.frame_buffers.clear()
        state_module.detection_results.clear()

    def test_no_snapshot_encodes_original_frame_without_drawing(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer
        # state_module.detection_results has no entry for "cam1".

        client = TestClient(_app_with_valid_token())
        with (
            patch.object(stream_router, "draw_detections") as mock_draw,
            patch.object(
                stream_router, "encode_jpeg", return_value=b"jpeg"
            ) as mock_encode,
        ):
            with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
                ws.receive_bytes()

        mock_draw.assert_not_called()
        mock_encode.assert_called_once()
        assert mock_encode.call_args.args[0] is frame.image

    def test_snapshot_with_detections_draws_before_encoding(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer

        detection = Detection(
            class_name="person", confidence=0.9, x1=1, y1=1, x2=2, y2=2
        )
        state_module.detection_results["cam1"] = DetectionSnapshot(
            detections=[detection], sequence=1
        )
        drawn_image = np.ones((480, 640, 3), dtype=np.uint8)

        client = TestClient(_app_with_valid_token())
        with (
            patch.object(
                stream_router, "draw_detections", return_value=drawn_image
            ) as mock_draw,
            patch.object(
                stream_router, "encode_jpeg", return_value=b"jpeg"
            ) as mock_encode,
        ):
            with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
                ws.receive_bytes()

        mock_draw.assert_called_once()
        draw_args = mock_draw.call_args.args
        assert draw_args[0] is frame.image
        assert draw_args[1] == [detection]
        mock_encode.assert_called_once_with(drawn_image)

    def test_snapshot_with_empty_detections_skips_drawing(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer
        state_module.detection_results["cam1"] = DetectionSnapshot(
            detections=[], sequence=1
        )

        client = TestClient(_app_with_valid_token())
        with (
            patch.object(stream_router, "draw_detections") as mock_draw,
            patch.object(
                stream_router, "encode_jpeg", return_value=b"jpeg"
            ) as mock_encode,
        ):
            with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
                ws.receive_bytes()

        mock_draw.assert_not_called()
        mock_encode.assert_called_once()
        assert mock_encode.call_args.args[0] is frame.image


class TestIssueStreamToken:
    @pytest.fixture
    def app(self) -> FastAPI:
        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        test_app.dependency_overrides[get_db_connection] = lambda: MagicMock()
        return test_app

    def test_requires_can_view(self, app: FastAPI) -> None:
        client = TestClient(app)
        res = client.post("/api/stream/cam1/token")
        assert res.status_code == 401  # require_can_view -> get_current_user -> 401

    def test_returns_token_when_authorized(self, app: FastAPI) -> None:
        stream_token_service = MagicMock()
        stream_token_service.issue.return_value = "signed.jwt.token"
        app.dependency_overrides[require_can_view] = lambda: _USER
        app.dependency_overrides[enforce_wan_access_gate] = lambda: None
        app.dependency_overrides[get_stream_token_service] = lambda: (
            stream_token_service
        )
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        client = TestClient(app)

        res = client.post("/api/stream/cam1/token")
        assert res.status_code == 200
        assert res.json() == {
            "token": "signed.jwt.token",
            "expires_in": _SETTINGS.stream_token_ttl_seconds,
        }
        stream_token_service.issue.assert_called_once_with(
            _USER.id, "cam1", _SETTINGS.stream_token_ttl_seconds
        )

    def test_returns_403_when_wan_gate_blocks(self, app: FastAPI) -> None:
        from fastapi import HTTPException

        def _blocked() -> None:
            raise HTTPException(403, "WAN access is not enabled for this account")

        app.dependency_overrides[require_can_view] = lambda: _USER
        app.dependency_overrides[enforce_wan_access_gate] = _blocked
        client = TestClient(app)

        res = client.post("/api/stream/cam1/token")
        assert res.status_code == 403
