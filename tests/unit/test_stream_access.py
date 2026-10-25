"""Tests for StreamAccessService: re-checking an open stream's viewer."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.models.domain import Session, User
from app.services.stream_access import StreamAccessService
from app.services.stream_tokens import StreamTokenPayload

_PAYLOAD = StreamTokenPayload(user_id=1, camera_name="cam1", session_jti="sid-1")
_USER = User(
    id=1,
    email="viewer@example.com",
    password_hash="hashed",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")


_SESSION = Session(
    jti="sid-1",
    user_id=1,
    issued_at="t",
    expires_at=_iso(timedelta(hours=1)),
    revoked_at=None,
    user_agent=None,
    ip_address=None,
)


class _Fakes:
    def __init__(self) -> None:
        self.sessions = AsyncMock()
        self.sessions.get.return_value = _SESSION
        self.users = AsyncMock()
        self.users.get_by_id.return_value = _USER
        self.authz = AsyncMock()
        self.authz.can_view.return_value = True
        self.authz.can_listen.return_value = True
        self.wan_gate = AsyncMock()
        self.wan_gate.is_active.return_value = True
        self.service = StreamAccessService(
            self.sessions, self.users, self.authz, self.wan_gate
        )


@pytest.fixture
def fakes() -> _Fakes:
    return _Fakes()


class TestIsGranted:
    async def test_granted_while_everything_still_allows_it(
        self, fakes: _Fakes
    ) -> None:
        assert await fakes.service.is_granted(_PAYLOAD, wan=False)
        fakes.authz.can_view.assert_awaited_once_with(1, "cam1")

    async def test_token_without_session_is_denied(self, fakes: _Fakes) -> None:
        payload = replace(_PAYLOAD, session_jti=None)
        assert not await fakes.service.is_granted(payload, wan=False)

    async def test_missing_session_is_denied(self, fakes: _Fakes) -> None:
        fakes.sessions.get.return_value = None
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_revoked_session_is_denied(self, fakes: _Fakes) -> None:
        fakes.sessions.get.return_value = replace(_SESSION, revoked_at="t")
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_session_of_another_user_is_denied(self, fakes: _Fakes) -> None:
        fakes.sessions.get.return_value = replace(_SESSION, user_id=2)
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_expired_session_is_denied(self, fakes: _Fakes) -> None:
        fakes.sessions.get.return_value = replace(
            _SESSION, expires_at=_iso(timedelta(seconds=-1))
        )
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_unparsable_session_expiry_is_denied(self, fakes: _Fakes) -> None:
        fakes.sessions.get.return_value = replace(_SESSION, expires_at="garbage")
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_naive_session_expiry_is_read_as_utc(self, fakes: _Fakes) -> None:
        future = datetime.now(UTC) + timedelta(hours=1)
        fakes.sessions.get.return_value = replace(
            _SESSION, expires_at=future.strftime("%Y-%m-%dT%H:%M:%S")
        )
        assert await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_inactive_user_is_denied(self, fakes: _Fakes) -> None:
        fakes.users.get_by_id.return_value = replace(_USER, is_active=False)
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_deleted_user_is_denied(self, fakes: _Fakes) -> None:
        fakes.users.get_by_id.return_value = None
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_lost_can_view_is_denied(self, fakes: _Fakes) -> None:
        fakes.authz.can_view.return_value = False
        assert not await fakes.service.is_granted(_PAYLOAD, wan=False)

    async def test_audio_stream_requires_can_listen(self, fakes: _Fakes) -> None:
        fakes.authz.can_listen.return_value = False
        payload = replace(_PAYLOAD, audio=True)
        assert not await fakes.service.is_granted(payload, wan=False)
        fakes.authz.can_listen.assert_awaited_once_with(1, "cam1")

    async def test_wan_viewer_needs_an_open_wan_gate(self, fakes: _Fakes) -> None:
        fakes.wan_gate.is_active.return_value = False
        assert not await fakes.service.is_granted(_PAYLOAD, wan=True)

    async def test_lan_viewer_ignores_the_wan_gate(self, fakes: _Fakes) -> None:
        fakes.wan_gate.is_active.return_value = False
        assert await fakes.service.is_granted(_PAYLOAD, wan=False)
        fakes.wan_gate.is_active.assert_not_awaited()
