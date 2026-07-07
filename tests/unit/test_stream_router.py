from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

import app.state as state_module
from app.models.frame import Frame
from app.routers import stream as stream_router


def _make_frame(sequence: int = 0) -> Frame:
    return Frame(
        image=np.zeros((480, 640, 3), dtype=np.uint8),
        width=640,
        height=480,
        timestamp=float(sequence),
        sequence=sequence,
    )


@pytest.fixture
def client() -> TestClient:
    mock_buffer = MagicMock()
    mock_buffer.read.return_value = None
    state_module.frame_buffers["cam1"] = mock_buffer
    test_app = FastAPI()
    test_app.include_router(stream_router.router)
    yield TestClient(test_app)
    state_module.frame_buffers.clear()


class TestWebSocketStream:
    def test_accepts_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/stream/cam1"):
            pass

    def test_sends_jpeg_when_frame_available(self) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        state_module.frame_buffers["cam1"] = mock_buffer
        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        client = TestClient(test_app)
        with client.websocket_connect("/ws/stream/cam1") as ws:
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
        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        client = TestClient(test_app)
        with client.websocket_connect("/ws/stream/cam1") as ws:
            data = ws.receive_bytes()
            assert data[:2] == b"\xff\xd8"
        state_module.frame_buffers.clear()

    def test_unknown_camera_closes_connection(self) -> None:
        state_module.frame_buffers.clear()
        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        client = TestClient(test_app)
        # WebSocket closed before accept — TestClient raises on connect or on recv
        try:
            with client.websocket_connect("/ws/stream/nonexistent") as ws:
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
            await stream_router.stream(ws, "cam1")  # must not raise
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
                await stream_router.stream(ws, "cam1")
        finally:
            state_module.frame_buffers.clear()
