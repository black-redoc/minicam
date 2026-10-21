"""Tests for the health router: public summary and admin per-camera view."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.state as state_module
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import require_admin
from app.models.domain import User
from app.routers import health as health_router
from tests.fixtures.camera import make_frame

_ADMIN = User(
    id=1,
    email="admin@example.com",
    password_hash="hashed",
    is_admin=True,
    is_active=True,
    created_at="t",
    updated_at="t",
)


def _camera(connected: bool) -> MagicMock:
    camera = MagicMock()
    camera.is_connected.return_value = connected
    return camera


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    yield
    state_module.cameras.clear()
    state_module.frame_buffers.clear()
    state_module.detection_workers.clear()


def _client(admin: bool = True) -> TestClient:
    test_app = FastAPI()
    test_app.include_router(health_router.router)
    if admin:
        test_app.dependency_overrides[require_admin] = lambda: _ADMIN
    else:

        def _deny() -> User:
            raise HTTPException(status_code=403, detail="Admin privileges required")

        test_app.dependency_overrides[require_admin] = _deny
    return TestClient(test_app)


class TestPublicHealth:
    def test_ok_when_every_camera_is_connected(self) -> None:
        state_module.cameras["a"] = _camera(True)
        state_module.cameras["b"] = _camera(True)

        res = _client().get("/health")

        assert res.status_code == 200
        assert res.json() == {"status": "ok", "cameras": 2, "cameras_connected": 2}

    def test_degraded_but_still_200_when_a_camera_is_down(self) -> None:
        state_module.cameras["a"] = _camera(True)
        state_module.cameras["b"] = _camera(False)

        res = _client().get("/health")

        assert res.status_code == 200
        assert res.json() == {
            "status": "degraded",
            "cameras": 2,
            "cameras_connected": 1,
        }

    def test_ok_with_no_cameras(self) -> None:
        assert _client().get("/health").json()["status"] == "ok"

    def test_does_not_expose_camera_names_or_debug(self) -> None:
        state_module.cameras["front-door"] = _camera(True)

        body = _client().get("/health").text

        assert "front-door" not in body
        assert "debug" not in body


class TestCameraHealth:
    def test_requires_admin(self) -> None:
        assert _client(admin=False).get("/api/health/cameras").status_code == 403

    def test_reports_each_camera_sorted_by_name(self) -> None:
        state_module.cameras["b"] = _camera(False)
        state_module.cameras["a"] = _camera(True)
        buf = FrameBuffer()
        buf.write(make_frame(0))
        state_module.frame_buffers["a"] = buf
        state_module.frame_buffers["b"] = FrameBuffer()

        with patch.object(health_router.time, "monotonic", return_value=1e12):
            res = _client().get("/api/health/cameras")

        assert res.status_code == 200
        body = res.json()
        assert [c["name"] for c in body] == ["a", "b"]
        assert body[0]["connected"] is True
        assert body[0]["last_frame_age_seconds"] > 0
        assert body[1]["connected"] is False
        assert body[1]["last_frame_age_seconds"] is None

    def test_missing_buffer_reports_no_frame_age(self) -> None:
        state_module.cameras["a"] = _camera(True)

        body = _client().get("/api/health/cameras").json()

        assert body[0]["last_frame_age_seconds"] is None

    async def test_detection_active_reflects_worker_task_state(self) -> None:
        state_module.cameras["running"] = _camera(True)
        state_module.cameras["finished"] = _camera(True)
        state_module.cameras["none"] = _camera(True)
        running = asyncio.create_task(asyncio.sleep(10))
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        state_module.detection_workers["running"] = running
        state_module.detection_workers["finished"] = finished
        try:
            body = health_router.camera_health()
        finally:
            running.cancel()

        active = {c.name: c.detection_active for c in body}
        assert active == {"finished": False, "none": False, "running": True}
