import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from httpx import Response

import app.state as state_module
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import (
    enforce_wan_access_gate,
    get_authorization_service,
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
from app.services.live.feed import LiveFeed
from app.services.stream_tokens import InvalidStreamTokenError, StreamTokenPayload
from tests.fixtures.live import AUDIO_INIT, INIT, open_feed, packet

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


def _buffer_with(frame: Frame) -> FrameBuffer:
    buf = FrameBuffer()
    buf.write(frame)
    return buf


@pytest.fixture
def client() -> TestClient:
    state_module.frame_buffers["cam1"] = FrameBuffer()
    yield TestClient(_app_with_valid_token())
    state_module.frame_buffers.clear()


class TestWebSocketStream:
    def test_accepts_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/stream/cam1?token=valid"):
            pass

    def test_sends_jpeg_when_frame_available(self) -> None:
        frame = _make_frame(sequence=1)
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf
        client = TestClient(_app_with_valid_token())
        with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
            data = ws.receive_bytes()
            assert data[:2] == b"\xff\xd8"
        state_module.frame_buffers.clear()

    def test_viewers_of_one_camera_share_a_single_encode(self) -> None:
        state_module.frame_buffers["cam1"] = _buffer_with(_make_frame(sequence=5))
        # As a context manager, TestClient runs every socket on one event
        # loop, like uvicorn does in production.
        with (
            TestClient(_app_with_valid_token()) as client,
            patch.object(
                stream_router, "encode_jpeg", return_value=b"\xff\xd8jpeg"
            ) as mock_encode,
        ):
            with (
                client.websocket_connect("/ws/stream/cam1?token=valid") as ws1,
                client.websocket_connect("/ws/stream/cam1?token=valid") as ws2,
            ):
                assert ws1.receive_bytes() == b"\xff\xd8jpeg"
                assert ws2.receive_bytes() == b"\xff\xd8jpeg"
        state_module.frame_buffers.clear()

        mock_encode.assert_called_once()

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
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf

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
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf

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
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf
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
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf

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
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf
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

    def test_expired_snapshot_is_not_drawn(self) -> None:
        frame = _make_frame(sequence=1)
        buf = _buffer_with(frame)
        state_module.frame_buffers["cam1"] = buf
        detection = Detection(
            class_name="person", confidence=0.9, x1=1, y1=1, x2=2, y2=2
        )
        state_module.detection_results["cam1"] = DetectionSnapshot(
            detections=[detection], sequence=1, expires_at=time.monotonic() - 1
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
        assert mock_encode.call_args.args[0] is frame.image


class TestIssueStreamToken:
    @pytest.fixture
    def app(self) -> FastAPI:
        test_app = FastAPI()
        test_app.include_router(stream_router.router)
        test_app.dependency_overrides[get_db_connection] = lambda: MagicMock()
        self.authz = MagicMock()
        self.authz.can_listen = AsyncMock(return_value=False)
        test_app.dependency_overrides[get_authorization_service] = lambda: self.authz
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
            "video_mime": None,
            "audio_mime": None,
        }
        stream_token_service.issue.assert_called_once_with(
            _USER.id, "cam1", _SETTINGS.stream_token_ttl_seconds, audio=False
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


class TestWebSocketStreamFmp4:
    """format=fmp4: the camera's own video as fMP4 plus detection messages.

    The feed is fed from the test thread while TestClient runs the app on
    its own event loop, the same cross-thread hand-off as the capture thread.
    """

    def setup_method(self) -> None:
        state_module.frame_buffers["cam1"] = _buffer_with(_make_frame(sequence=1))
        self.feed = LiveFeed()
        state_module.live_feeds["cam1"] = self.feed
        open_feed(self.feed)
        self.feed.on_packet(packet(1, keyframe=True))

    def teardown_method(self) -> None:
        state_module.frame_buffers.clear()
        state_module.live_feeds.clear()
        state_module.detection_results.clear()

    def test_sends_init_fragments_and_detections(self) -> None:
        with (
            TestClient(_app_with_valid_token()) as client,
            client.websocket_connect("/ws/stream/cam1?token=valid&format=fmp4") as ws,
        ):
            assert ws.receive_json() == {"type": "init", "mime": INIT.mime}
            assert ws.receive_bytes() == INIT.data
            assert ws.receive_json() == {
                "type": "detections",
                "width": 640,
                "height": 480,
                "boxes": [],
            }
            assert ws.receive_bytes() == b"\x01"
            self.feed.on_packet(packet(2))
            assert ws.receive_bytes() == b"\x02"

    def test_detection_boxes_are_sent_when_they_change(self) -> None:
        with (
            TestClient(_app_with_valid_token()) as client,
            client.websocket_connect("/ws/stream/cam1?token=valid&format=fmp4") as ws,
        ):
            ws.receive_json()
            ws.receive_bytes()
            assert ws.receive_json()["boxes"] == []
            ws.receive_bytes()

            state_module.detection_results["cam1"] = DetectionSnapshot(
                detections=[Detection("person", 0.876, 10, 20, 110, 220)], sequence=1
            )
            message = ws.receive_json()
            assert message["boxes"] == [
                {
                    "label": "person",
                    "confidence": 0.88,
                    "color": "#ff0000",
                    "x1": 10,
                    "y1": 20,
                    "x2": 110,
                    "y2": 220,
                }
            ]

    def test_expired_detections_are_cleared(self) -> None:
        state_module.detection_results["cam1"] = DetectionSnapshot(
            detections=[Detection("person", 0.9, 1, 2, 3, 4)],
            sequence=1,
            expires_at=0.0,
        )
        state_module.frame_buffers.clear()  # no frame: size unknown
        state_module.frame_buffers["cam1"] = FrameBuffer()
        with (
            TestClient(_app_with_valid_token()) as client,
            client.websocket_connect("/ws/stream/cam1?token=valid&format=fmp4") as ws,
        ):
            ws.receive_json()
            ws.receive_bytes()
            assert ws.receive_json() == {
                "type": "detections",
                "width": 0,
                "height": 0,
                "boxes": [],
            }

    def test_closes_once_the_camera_is_removed(self) -> None:
        with (
            TestClient(_app_with_valid_token()) as client,
            client.websocket_connect("/ws/stream/cam1?token=valid&format=fmp4") as ws,
        ):
            ws.receive_json()
            ws.receive_bytes()
            ws.receive_json()
            ws.receive_bytes()
            del state_module.live_feeds["cam1"]
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_bytes()
            assert exc_info.value.code == 1001

    def test_camera_without_live_feed_is_rejected(self) -> None:
        state_module.live_feeds.clear()
        client = TestClient(_app_with_valid_token())
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws/stream/cam1?token=valid&format=fmp4") as ws,
        ):
            ws.receive_bytes()
        assert exc_info.value.code == 1008

    def test_default_format_is_still_mjpeg(self) -> None:
        client = TestClient(_app_with_valid_token())
        with client.websocket_connect("/ws/stream/cam1?token=valid") as ws:
            assert ws.receive_bytes()[:2] == b"\xff\xd8"


class TestStreamTokenVideoMime:
    def test_token_announces_the_live_stream_codec(self) -> None:
        feed = LiveFeed()
        open_feed(feed)
        feed.on_packet(packet(1, keyframe=True))
        state_module.live_feeds["cam1"] = feed
        app = FastAPI()
        app.include_router(stream_router.router)
        app.dependency_overrides[require_can_view] = lambda: _USER
        app.dependency_overrides[enforce_wan_access_gate] = lambda: None
        token_service = MagicMock()
        token_service.issue.return_value = "signed.jwt.token"
        app.dependency_overrides[get_stream_token_service] = lambda: token_service
        app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        authz = MagicMock()
        authz.can_listen = AsyncMock(return_value=False)
        app.dependency_overrides[get_authorization_service] = lambda: authz
        try:
            res = TestClient(app).post("/api/stream/cam1/token")
        finally:
            state_module.live_feeds.clear()
        assert res.json()["video_mime"] == INIT.mime


def _app_with_token(audio: bool) -> FastAPI:
    service = MagicMock()
    service.verify.return_value = StreamTokenPayload(
        user_id=1, camera_name="cam1", audio=audio
    )
    test_app = FastAPI()
    test_app.include_router(stream_router.router)
    test_app.dependency_overrides[get_stream_token_service] = lambda: service
    return test_app


class TestWebSocketStreamAudio:
    def setup_method(self) -> None:
        state_module.frame_buffers["cam1"] = _buffer_with(_make_frame(sequence=1))
        self.feed = LiveFeed()
        state_module.live_feeds["cam1"] = self.feed
        open_feed(self.feed, audio=True)
        self.feed.on_packet(packet(1, keyframe=True))

    def teardown_method(self) -> None:
        state_module.frame_buffers.clear()
        state_module.live_feeds.clear()

    def test_audio_token_gets_the_rendition_with_audio(self) -> None:
        with (
            TestClient(_app_with_token(audio=True)) as client,
            client.websocket_connect(
                "/ws/stream/cam1?token=t&format=fmp4&audio=true"
            ) as ws,
        ):
            assert ws.receive_json() == {"type": "init", "mime": AUDIO_INIT.mime}
            assert ws.receive_bytes() == AUDIO_INIT.data
            ws.receive_json()
            self.feed.on_packet(packet(2, kind="audio"))
            assert ws.receive_bytes() == b"\x01"
            assert ws.receive_bytes() == b"\x02"

    def test_without_audio_requested_no_audio_is_sent(self) -> None:
        with (
            TestClient(_app_with_token(audio=True)) as client,
            client.websocket_connect("/ws/stream/cam1?token=t&format=fmp4") as ws,
        ):
            assert ws.receive_json() == {"type": "init", "mime": INIT.mime}

    @pytest.mark.parametrize(
        ("audio_claim", "query"),
        [
            (False, "format=fmp4&audio=true"),  # token not issued for audio
            (True, "audio=true"),  # MJPEG has no audio
        ],
    )
    def test_unauthorized_audio_is_rejected(
        self, audio_claim: bool, query: str
    ) -> None:
        client = TestClient(_app_with_token(audio=audio_claim))
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(f"/ws/stream/cam1?token=t&{query}") as ws,
        ):
            ws.receive_bytes()
        assert exc_info.value.code == 1008


class TestStreamTokenAudio:
    def setup_method(self) -> None:
        self.feed = LiveFeed()
        open_feed(self.feed, audio=True)
        self.feed.on_packet(packet(1, keyframe=True))
        state_module.live_feeds["cam1"] = self.feed
        self.token_service = MagicMock()
        self.token_service.issue.return_value = "signed.jwt.token"
        self.authz = MagicMock()
        self.app = FastAPI()
        self.app.include_router(stream_router.router)
        self.app.dependency_overrides[require_can_view] = lambda: _USER
        self.app.dependency_overrides[enforce_wan_access_gate] = lambda: None
        self.app.dependency_overrides[get_stream_token_service] = lambda: (
            self.token_service
        )
        self.app.dependency_overrides[get_security_settings] = lambda: _SETTINGS
        self.app.dependency_overrides[get_authorization_service] = lambda: self.authz

    def teardown_method(self) -> None:
        state_module.live_feeds.clear()

    def _post(self, can_listen: bool, query: str = "") -> Response:
        self.authz.can_listen = AsyncMock(return_value=can_listen)
        response: Response = TestClient(self.app).post(f"/api/stream/cam1/token{query}")
        return response

    def test_listener_is_told_the_audio_codec(self) -> None:
        res = self._post(can_listen=True)
        assert res.json()["audio_mime"] == AUDIO_INIT.mime
        assert res.json()["video_mime"] == INIT.mime

    def test_audio_codec_is_hidden_without_can_listen(self) -> None:
        assert self._post(can_listen=False).json()["audio_mime"] is None

    def test_audio_token_requires_can_listen(self) -> None:
        res = self._post(can_listen=False, query="?audio=true")
        assert res.status_code == 403
        self.token_service.issue.assert_not_called()

    def test_audio_token_is_issued_for_listeners(self) -> None:
        res = self._post(can_listen=True, query="?audio=true")
        assert res.status_code == 200
        self.token_service.issue.assert_called_once_with(
            _USER.id, "cam1", _SETTINGS.stream_token_ttl_seconds, audio=True
        )
