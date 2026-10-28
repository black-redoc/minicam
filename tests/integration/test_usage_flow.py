"""Integration: WAN usage metering (minicam-admin ADR 0008, specs/012 AC6).

In central mode every WAN live-stream frame adds its length to the usage
meter under the viewer's email and the camera; LAN streams and recording
clips add nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.state as state_module
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import enforce_wan_access_gate, get_network_zone
from app.models.frame import Frame
from app.repositories.entitlement_sync_repository import (
    SQLiteEntitlementSyncRepository,
)
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.recording_repository import SQLiteRecordingRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.routers import recordings as recordings_router
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher
from app.services.network import NetworkZone
from app.services.recording.runtime import RecordingService
from app.services.usage import UsageMeter

_VIEWER = "Viewer@Example.com"


@pytest.fixture(autouse=True)
def central_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[UsageMeter]:
    monkeypatch.setenv("MINICAM_CENTRAL_URL", "https://admin.example.com")
    monkeypatch.setenv("MINICAM_DEVICE_TOKEN", "mcd_test_token")
    get_security_settings.cache_clear()
    meter = state_module.usage_meter = UsageMeter()
    buf = FrameBuffer()
    buf.write(
        Frame(
            image=np.zeros((4, 4, 3), dtype=np.uint8),
            width=4,
            height=4,
            timestamp=0.0,
            sequence=0,
        )
    )
    state_module.frame_buffers["cam1"] = buf
    yield meter
    state_module.usage_meter = None
    state_module.frame_buffers.clear()
    get_security_settings.cache_clear()


async def _log_in(client: TestClient, db_connection: aiosqlite.Connection) -> None:
    """Log in on the LAN as an entitled viewer of cam1."""
    user = await SQLiteUserRepository(db_connection).create(
        _VIEWER, PasswordHasher().hash("pw"), is_admin=False
    )
    await SQLitePermissionRepository(db_connection).upsert(
        user.id, "cam1", can_view=True, can_use_ptz=False
    )
    await SQLiteEntitlementSyncRepository(db_connection).apply(
        {_VIEWER}, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )
    res = client.post("/api/auth/login", json={"email": _VIEWER, "password": "pw"})
    assert res.status_code == 200


def _stream_one_frame(client: TestClient, app: FastAPI, zone: NetworkZone) -> bytes:
    """Get a stream token on the LAN, then watch one frame from zone."""
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.LAN
    token = client.post("/api/stream/cam1/token").json()["token"]
    app.dependency_overrides[get_network_zone] = lambda: zone
    with client.websocket_connect(f"/ws/stream/cam1?token={token}") as ws:
        return ws.receive_bytes()


async def test_wan_stream_bytes_are_counted(
    app: FastAPI,
    client: TestClient,
    db_connection: aiosqlite.Connection,
    central_mode: UsageMeter,
) -> None:
    await _log_in(client, db_connection)

    jpeg = _stream_one_frame(client, app, NetworkZone.WAN)

    assert central_mode.pending() == {("viewer@example.com", "cam1"): len(jpeg)}


async def test_lan_and_clip_bytes_are_not_counted(
    app: FastAPI,
    client: TestClient,
    db_connection: aiosqlite.Connection,
    central_mode: UsageMeter,
    tmp_path: Path,
) -> None:
    await _log_in(client, db_connection)
    assert _stream_one_frame(client, app, NetworkZone.LAN)

    # A clip downloaded over WAN is not live streaming.
    app.include_router(recordings_router.router)
    app.dependency_overrides[recordings_router.get_recording_service] = lambda: (
        RecordingService(tmp_path, connection=MagicMock(), config=MagicMock())
    )
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    app.dependency_overrides[enforce_wan_access_gate] = lambda: None  # not under test
    recording = await SQLiteRecordingRepository(db_connection).add(
        "cam1", "clip.mp4", "2026-10-27T12:00:00+00:00", 1.0, 10, ("person",)
    )
    (tmp_path / "clip.mp4").write_bytes(b"0123456789")
    res = client.get(f"/api/recordings/{recording.id}/video")
    assert res.status_code == 200
    assert res.content == b"0123456789"

    assert central_mode.pending() == {}
