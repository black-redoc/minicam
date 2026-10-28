"""Re-checks, while a stream is open, that its viewer may still watch it.

A stream token is only verified at the WebSocket handshake, and the socket
then stays open on its own. Without this check, logging out, revoking the
session, deactivating the user, removing can_view/can_listen or closing the
WAN access gate would not stop a stream that is already playing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.services.authorization import AuthorizationService
from app.services.stream_tokens import StreamTokenPayload
from app.services.wan_access import WanAccessGateService


class StreamAccessService:
    def __init__(
        self,
        session_repository: SessionRepository,
        user_repository: UserRepository,
        authorization_service: AuthorizationService,
        wan_access_gate: WanAccessGateService,
    ) -> None:
        self._session_repository = session_repository
        self._user_repository = user_repository
        self._authorization_service = authorization_service
        self._wan_access_gate = wan_access_gate

    async def is_granted(self, payload: StreamTokenPayload, wan: bool) -> bool:
        """True while the token's session, user and permissions still allow
        this stream (and, for a WAN viewer, the WAN access gate is open)."""
        if payload.session_jti is None:
            return False
        session = await self._session_repository.get(payload.session_jti)
        if (
            session is None
            or session.revoked_at is not None
            or session.user_id != payload.user_id
            or _is_past(session.expires_at)
        ):
            return False
        user = await self._user_repository.get_by_id(payload.user_id)
        if user is None or not user.is_active:
            return False
        if payload.audio:
            allowed = await self._authorization_service.can_listen(
                user.id, payload.camera_name
            )
        else:
            allowed = await self._authorization_service.can_view(
                user.id, payload.camera_name
            )
        if not allowed:
            return False
        return not wan or await self._wan_access_gate.is_active(user.id)

    async def viewer_email(self, payload: StreamTokenPayload) -> str | None:
        """The email of the token's user, to count their WAN usage under."""
        user = await self._user_repository.get_by_id(payload.user_id)
        return user.email if user is not None else None


def _is_past(timestamp: str) -> bool:
    """Fail closed: an unparsable expiry counts as already past."""
    try:
        expires_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)
