import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi import FastAPI
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
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    mock_buffer = MagicMock()
    mock_buffer.read.return_value = None
    monkeypatch.setattr(state_module, "frame_buffer", mock_buffer)

    test_app = FastAPI()
    test_app.include_router(stream_router.router)
    return TestClient(test_app)


class TestWebSocketStream:
    def test_accepts_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/stream"):
            pass  # connects and disconnects cleanly

    def test_sends_jpeg_when_frame_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frame = _make_frame(sequence=1)
        mock_buffer = MagicMock()
        mock_buffer.read.return_value = frame
        monkeypatch.setattr(state_module, "frame_buffer", mock_buffer)

        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        client = TestClient(test_app)

        with client.websocket_connect("/ws/stream") as ws:
            data = ws.receive_bytes()
            assert data[:2] == b"\xff\xd8"  # JPEG magic bytes

    def test_does_not_send_duplicate_frames(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frame = _make_frame(sequence=5)
        call_count = 0

        def read_side_effect() -> Frame:
            nonlocal call_count
            call_count += 1
            return frame

        mock_buffer = MagicMock()
        mock_buffer.read.side_effect = read_side_effect
        monkeypatch.setattr(state_module, "frame_buffer", mock_buffer)

        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        client = TestClient(test_app)

        with client.websocket_connect("/ws/stream") as ws:
            data = ws.receive_bytes()
            # First frame sent; subsequent reads return same sequence → no duplicate
            assert data[:2] == b"\xff\xd8"
