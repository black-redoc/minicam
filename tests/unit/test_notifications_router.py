"""Tests for the notifications router: settings, SSE feed, Web Push opt-in."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.dependencies import (
    enforce_wan_access_gate,
    get_authorization_service,
    get_current_user,
    get_push_subscription_repository,
    require_admin,
)
from app.models.config import AppConfig, NotificationConfig
from app.models.domain import User
from app.routers import notifications as notifications_router
from app.services.config_writer import load_config
from app.services.notifications.events import DetectionEvent, EventHub
from app.services.notifications.web_push import b64url_encode

_USER = User(
    id=7,
    email="user@example.com",
    password_hash="h",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)
_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


def _browser_keys() -> dict[str, str]:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    return {"p256dh": b64url_encode(public), "auth": b64url_encode(b"0" * 16)}


def _event(camera: str) -> DetectionEvent:
    return DetectionEvent(camera, ("person",), datetime(2026, 10, 21, tzinfo=UTC))


@pytest.fixture
def repository() -> MagicMock:
    repo = MagicMock()
    repo.upsert = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def sender() -> MagicMock:
    mock = MagicMock()
    mock.public_key = "PUBLIC"
    return mock


@pytest.fixture
def app(repository: MagicMock, sender: MagicMock) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(notifications_router.router)
    test_app.dependency_overrides[get_current_user] = lambda: _USER
    test_app.dependency_overrides[require_admin] = lambda: _USER
    test_app.dependency_overrides[enforce_wan_access_gate] = lambda: None
    test_app.dependency_overrides[get_push_subscription_repository] = lambda: repository
    test_app.dependency_overrides[notifications_router.get_push_sender] = lambda: sender
    return test_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    yield TestClient(app)


class TestSettings:
    def test_get_returns_current_settings(self, client: TestClient) -> None:
        config = AppConfig(notifications=NotificationConfig(enabled=True))
        with patch.object(notifications_router, "get_config", return_value=config):
            res = client.get("/api/notifications/settings")
        assert res.json() == {"enabled": True, "cooldown_seconds": 60}

    def test_put_persists_settings(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.services.config_writer as config_writer_module

        path = tmp_path / "config.toml"
        monkeypatch.setattr(config_writer_module, "_CONFIG_PATH", path)
        monkeypatch.setattr(
            notifications_router,
            "update_notification_config",
            lambda body: config_writer_module.update_notification_config(body, path),
        )
        res = client.put(
            "/api/notifications/settings",
            json={"enabled": True, "cooldown_seconds": 120},
        )
        assert res.status_code == 200
        assert load_config(path).notifications == NotificationConfig(
            enabled=True, cooldown_seconds=120
        )

    @pytest.mark.parametrize("cooldown", [0, 86401])
    def test_put_rejects_out_of_range_cooldown(
        self, client: TestClient, cooldown: int
    ) -> None:
        res = client.put(
            "/api/notifications/settings",
            json={"enabled": True, "cooldown_seconds": cooldown},
        )
        assert res.status_code == 422

    def test_settings_require_admin(self, app: FastAPI) -> None:
        def deny() -> User:
            raise HTTPException(403, "Admin privileges required")

        app.dependency_overrides[require_admin] = deny
        client = TestClient(app)
        assert client.get("/api/notifications/settings").status_code == 403
        res = client.put("/api/notifications/settings", json={"enabled": True})
        assert res.status_code == 403


class _Authz:
    def __init__(self, viewable: set[str]) -> None:
        self._viewable = viewable

    async def can_view(self, user_id: int, camera_name: str) -> bool:
        return camera_name in self._viewable


class TestEventStream:
    async def test_yields_only_viewable_events_and_keepalives(self) -> None:
        hub = EventHub()
        stream = notifications_router.detection_event_stream(
            7,
            hub,
            _Authz({"cam1"}),  # type: ignore[arg-type]
            heartbeat_seconds=0.01,
            max_stream_seconds=5,
        )
        assert await anext(stream) == "retry: 3000\n\n"
        assert hub.subscriber_count == 1  # subscribed before the first frame

        next_frame = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        hub.publish(_event("secret-cam"))
        hub.publish(_event("cam1"))
        frame = await next_frame
        assert frame.startswith("event: detection\ndata: ")
        assert '"camera": "cam1"' in frame
        assert "secret-cam" not in frame

        assert await anext(stream) == ": keepalive\n\n"
        await stream.aclose()
        assert hub.subscriber_count == 0

    async def test_ends_after_max_duration(self) -> None:
        stream = notifications_router.detection_event_stream(
            7,
            EventHub(),
            _Authz(set()),  # type: ignore[arg-type]
            heartbeat_seconds=0.01,
            max_stream_seconds=0.02,
        )
        frames = [frame async for frame in stream]
        assert frames[0] == "retry: 3000\n\n"
        assert set(frames[1:]) <= {": keepalive\n\n"}

    def test_endpoint_streams_event_source(
        self, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def short_stream(*_args: object) -> object:
            yield "retry: 3000\n\n"

        monkeypatch.setattr(
            notifications_router, "detection_event_stream", short_stream
        )
        app.dependency_overrides[get_authorization_service] = lambda: _Authz(set())
        with TestClient(app).stream("GET", "/api/notifications/events") as res:
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("text/event-stream")
            assert res.headers["cache-control"] == "no-cache"
            assert res.read() == b"retry: 3000\n\n"


class TestPushSubscriptions:
    def test_public_key(self, client: TestClient) -> None:
        res = client.get("/api/notifications/push/public-key")
        assert res.json() == {"public_key": "PUBLIC"}

    def test_public_key_503_without_sender(self, app: FastAPI) -> None:
        app.dependency_overrides[notifications_router.get_push_sender] = lambda: None
        res = TestClient(app).get("/api/notifications/push/public-key")
        assert res.status_code == 503

    def test_subscribe_stores_subscription(
        self, client: TestClient, repository: MagicMock
    ) -> None:
        keys = _browser_keys()
        res = client.post(
            "/api/notifications/push/subscriptions",
            json={"endpoint": _ENDPOINT, "keys": keys},
        )
        assert res.status_code == 201
        repository.upsert.assert_awaited_once_with(
            7, _ENDPOINT, keys["p256dh"], keys["auth"]
        )

    def test_subscribe_rejects_unknown_push_service(
        self, client: TestClient, repository: MagicMock
    ) -> None:
        res = client.post(
            "/api/notifications/push/subscriptions",
            json={"endpoint": "https://192.168.1.1/hook", "keys": _browser_keys()},
        )
        assert res.status_code == 400
        repository.upsert.assert_not_awaited()

    def test_subscribe_rejects_bad_keys(
        self, client: TestClient, repository: MagicMock
    ) -> None:
        res = client.post(
            "/api/notifications/push/subscriptions",
            json={"endpoint": _ENDPOINT, "keys": {"p256dh": "AAAA", "auth": "AAAA"}},
        )
        assert res.status_code == 400
        repository.upsert.assert_not_awaited()

    def test_subscribe_503_without_sender(self, app: FastAPI) -> None:
        app.dependency_overrides[notifications_router.get_push_sender] = lambda: None
        res = TestClient(app).post(
            "/api/notifications/push/subscriptions",
            json={"endpoint": _ENDPOINT, "keys": _browser_keys()},
        )
        assert res.status_code == 503

    def test_subscribe_enforces_wan_gate(self, app: FastAPI) -> None:
        def deny() -> None:
            raise HTTPException(403, "WAN access is not enabled for this account")

        app.dependency_overrides[enforce_wan_access_gate] = deny
        res = TestClient(app).post(
            "/api/notifications/push/subscriptions",
            json={"endpoint": _ENDPOINT, "keys": _browser_keys()},
        )
        assert res.status_code == 403

    def test_unsubscribe(self, client: TestClient, repository: MagicMock) -> None:
        res = client.request(
            "DELETE",
            "/api/notifications/push/subscriptions",
            json={"endpoint": _ENDPOINT},
        )
        assert res.status_code == 204
        repository.delete.assert_awaited_once_with(7, _ENDPOINT)

    def test_requires_login(self, app: FastAPI) -> None:
        def unauthenticated() -> User:
            raise HTTPException(401, "Not authenticated")

        app.dependency_overrides[get_current_user] = unauthenticated
        client = TestClient(app)
        assert client.get("/api/notifications/push/public-key").status_code == 401
        assert (
            client.request(
                "DELETE",
                "/api/notifications/push/subscriptions",
                json={"endpoint": _ENDPOINT},
            ).status_code
            == 401
        )


def test_default_dependencies_use_runtime_singletons() -> None:
    from app.services.notifications import runtime

    assert notifications_router.get_event_hub() is runtime.hub
    assert notifications_router.get_push_sender() is runtime.service.push_sender


def test_service_worker_is_served_from_root(client: TestClient) -> None:
    res = client.get("/service-worker.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")
    assert "showNotification" in res.text
