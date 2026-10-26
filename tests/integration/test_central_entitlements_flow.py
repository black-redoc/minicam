"""Integration: central mode (minicam-admin ADR 0007, specs/011 AC10, AC11).

The dashboard owns the wan_access flags, so the local toggle answers 409,
and an open WAN stream closes on the next re-check once a sync blocks its
user or the cache goes stale, while a LAN stream keeps playing.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import aiosqlite
import numpy as np
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

import app.state as state_module
from app.buffers.frame_buffer import FrameBuffer
from app.dependencies import get_network_zone
from app.models.frame import Frame
from app.repositories.entitlement_sync_repository import (
    SQLiteEntitlementSyncRepository,
)
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.repositories.wan_access_repository import SQLiteWanAccessRepository
from app.routers import stream as stream_router
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher
from app.services.network import NetworkZone

_VIEWER = "viewer@example.com"


@pytest.fixture(autouse=True)
def central_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MINICAM_CENTRAL_URL", "https://admin.example.com")
    monkeypatch.setenv("MINICAM_DEVICE_TOKEN", "mcd_test_token")
    get_security_settings.cache_clear()
    yield
    get_security_settings.cache_clear()


@pytest.fixture
def buffer() -> Iterator[FrameBuffer]:
    buf = FrameBuffer()
    _write_frame(buf, 0)
    state_module.frame_buffers["cam1"] = buf
    yield buf
    state_module.frame_buffers.clear()


def _write_frame(buf: FrameBuffer, sequence: int) -> None:
    buf.write(
        Frame(
            image=np.zeros((4, 4, 3), dtype=np.uint8),
            width=4,
            height=4,
            timestamp=float(sequence),
            sequence=sequence,
        )
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def _viewer_token(client: TestClient, db_connection: aiosqlite.Connection) -> str:
    """Log in on the LAN and get a stream token; the caller flips to WAN."""
    user = await SQLiteUserRepository(db_connection).create(
        _VIEWER, PasswordHasher().hash("pw"), is_admin=False
    )
    await SQLitePermissionRepository(db_connection).upsert(
        user.id, "cam1", can_view=True, can_use_ptz=False
    )
    res = client.post("/api/auth/login", json={"email": _VIEWER, "password": "pw"})
    assert res.status_code == 200
    token: str = client.post("/api/stream/cam1/token").json()["token"]
    return token


async def test_wan_access_locked_in_central_mode(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    users = SQLiteUserRepository(db_connection)
    await users.create("admin@example.com", PasswordHasher().hash("pw"), is_admin=True)
    viewer = await users.create(_VIEWER, PasswordHasher().hash("pw"), is_admin=False)
    res = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "pw"}
    )
    assert res.status_code == 200

    res = client.post(
        "/api/auth/wan-access", json={"user_id": viewer.id, "is_active": True}
    )

    assert res.status_code == 409
    assert await SQLiteWanAccessRepository(db_connection).get(viewer.id) is None
    # Reading the flag still works.
    assert client.get(f"/api/auth/wan-access?user_id={viewer.id}").status_code == 200


@pytest.mark.parametrize("cause", ["blocked", "stale"])
async def test_open_wan_stream_closes_when_blocked(
    app: FastAPI,
    client: TestClient,
    db_connection: aiosqlite.Connection,
    buffer: FrameBuffer,
    cause: str,
) -> None:
    token = await _viewer_token(client, db_connection)
    sync = SQLiteEntitlementSyncRepository(db_connection)
    await sync.apply({_VIEWER}, _now())
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    with (
        patch.object(stream_router, "_ACCESS_RECHECK_SECONDS", 0.02),
        client.websocket_connect(f"/ws/stream/cam1?token={token}") as ws,
    ):
        ws.receive_bytes()
        if cause == "blocked":
            await sync.apply(set(), _now())
        else:  # the dashboard has been unreachable for over 30 min
            stale = datetime.now(UTC) - timedelta(minutes=31)
            await sync.apply({_VIEWER}, stale.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_bytes()
    assert exc_info.value.code == 1008


async def test_wan_stream_rejected_before_the_first_sync(
    app: FastAPI,
    client: TestClient,
    db_connection: aiosqlite.Connection,
    buffer: FrameBuffer,
) -> None:
    token = await _viewer_token(client, db_connection)
    user = await SQLiteUserRepository(db_connection).get_by_email(_VIEWER)
    assert user is not None
    await SQLiteWanAccessRepository(db_connection).set_active(user.id, True)
    app.dependency_overrides[get_network_zone] = lambda: NetworkZone.WAN
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/ws/stream/cam1?token={token}") as ws,
    ):
        ws.receive_bytes()
    assert exc_info.value.code == 1008


async def test_lan_stream_keeps_playing_when_blocked(
    client: TestClient, db_connection: aiosqlite.Connection, buffer: FrameBuffer
) -> None:
    token = await _viewer_token(client, db_connection)
    sync = SQLiteEntitlementSyncRepository(db_connection)
    with (
        patch.object(stream_router, "_ACCESS_RECHECK_SECONDS", 0.02),
        client.websocket_connect(f"/ws/stream/cam1?token={token}") as ws,
    ):
        ws.receive_bytes()
        await sync.apply(set(), _now())
        time.sleep(0.1)  # several re-checks
        _write_frame(buffer, 1)
        assert ws.receive_bytes()
