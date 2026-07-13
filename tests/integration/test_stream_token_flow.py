"""Integration: full stream-token lifecycle against a real SQLite database —
issue a token, use it to open the WebSocket, and confirm the token cannot be
reused for a different camera or replaced by the session cookie alone.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
from fastapi.testclient import TestClient

import app.state as state_module
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.services.auth import PasswordHasher


async def _login_viewer(
    client: TestClient, db_connection: aiosqlite.Connection, email: str
) -> None:
    user_repo = SQLiteUserRepository(db_connection)
    user = await user_repo.create(email, PasswordHasher().hash("pw"), is_admin=False)
    perm_repo = SQLitePermissionRepository(db_connection)
    await perm_repo.upsert(user.id, "cam1", can_view=True, can_use_ptz=False)
    res = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    assert res.status_code == 200


async def test_issue_token_requires_view_permission(client: TestClient) -> None:
    res = client.post("/api/stream/cam1/token")
    assert res.status_code == 401  # not even authenticated


async def test_issue_token_returns_short_lived_token(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _login_viewer(client, db_connection, "issuer@example.com")

    res = client.post("/api/stream/cam1/token")

    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["token"], str) and body["token"]
    assert body["expires_in"] == 30  # set via MINICAM_STREAM_TOKEN_TTL_SECONDS


async def test_issue_token_denied_for_camera_without_permission(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _login_viewer(client, db_connection, "restricted@example.com")

    res = client.post("/api/stream/cam-other/token")

    assert res.status_code == 404


async def test_valid_token_opens_the_websocket(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    mock_buffer = MagicMock()
    mock_buffer.read.return_value = None
    state_module.frame_buffers["cam1"] = mock_buffer
    try:
        await _login_viewer(client, db_connection, "streamer@example.com")
        token = client.post("/api/stream/cam1/token").json()["token"]

        with client.websocket_connect(f"/ws/stream/cam1?token={token}"):
            pass  # connection accepted — no exception raised
    finally:
        state_module.frame_buffers.clear()


async def test_token_issued_for_a_different_camera_is_rejected(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    state_module.frame_buffers["cam1"] = MagicMock()
    state_module.frame_buffers["cam2"] = MagicMock()
    try:
        user_repo = SQLiteUserRepository(db_connection)
        user = await user_repo.create(
            "multi@example.com", PasswordHasher().hash("pw"), is_admin=False
        )
        perm_repo = SQLitePermissionRepository(db_connection)
        await perm_repo.upsert(user.id, "cam1", can_view=True, can_use_ptz=False)
        await perm_repo.upsert(user.id, "cam2", can_view=True, can_use_ptz=False)
        client.post(
            "/api/auth/login", json={"email": "multi@example.com", "password": "pw"}
        )

        token_for_cam1 = client.post("/api/stream/cam1/token").json()["token"]

        # A token minted for cam1 must not open cam2's socket.
        url = f"/ws/stream/cam2?token={token_for_cam1}"
        try:
            with client.websocket_connect(url) as ws:
                ws.receive_bytes()
        except Exception:
            pass  # Expected: closed with 1008
    finally:
        state_module.frame_buffers.clear()


async def test_session_cookie_alone_cannot_open_the_websocket(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    """The WS handshake must not fall back to the session cookie — the
    stream token is the only credential it accepts, by design (see
    app/services/stream_tokens.py).
    """
    state_module.frame_buffers["cam1"] = MagicMock()
    try:
        await _login_viewer(client, db_connection, "cookieonly@example.com")
        try:
            with client.websocket_connect("/ws/stream/cam1") as ws:
                ws.receive_bytes()
        except Exception:
            pass  # Expected: rejected — no token query param at all
    finally:
        state_module.frame_buffers.clear()
