"""Short-lived, stateless tokens that authorize a single WebSocket handshake.

Distinct from the session JWT (TokenService) on purpose: a stream token is
bound to one camera via the `camera` claim (and, with the `audio` claim,
lets the socket carry that camera's audio), carries a short TTL (~30s, see
security_settings.stream_token_ttl_seconds), and names the session it was
issued for (`sid`). Once the handshake succeeds the socket stays open even if
the token has since expired; the stream router re-checks that session and the
viewer's permissions while it plays (see app/services/stream_access.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

_STREAM_TOKEN_TYPE = "stream"


class InvalidStreamTokenError(Exception):
    """Raised when a stream token is missing, malformed, expired, of the
    wrong type (e.g. a session token reused as a stream token), or issued
    for a different camera than the one being requested.
    """


@dataclass(frozen=True, slots=True)
class StreamTokenPayload:
    user_id: int
    camera_name: str
    audio: bool = False
    session_jti: str | None = None


class StreamTokenService:
    def __init__(self, secret: str, algorithm: str = "HS256") -> None:
        self._secret = secret
        self._algorithm = algorithm

    def issue(
        self,
        user_id: int,
        camera_name: str,
        ttl_seconds: int,
        audio: bool = False,
        session_jti: str | None = None,
    ) -> str:
        """audio=True authorizes the camera's audio: only for can_listen.
        session_jti is the session the stream lives and dies with."""
        now = int(time.time())
        payload: dict[str, object] = {
            "sub": str(user_id),
            "camera": camera_name,
            "typ": _STREAM_TOKEN_TYPE,
            "iat": now,
            "exp": now + ttl_seconds,
        }
        if audio:
            payload["audio"] = True
        if session_jti is not None:
            payload["sid"] = session_jti
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def verify(self, token: str, camera_name: str) -> StreamTokenPayload:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.PyJWTError as exc:
            raise InvalidStreamTokenError("Malformed or expired stream token") from exc

        if payload.get("typ") != _STREAM_TOKEN_TYPE:
            raise InvalidStreamTokenError("Token is not a stream token")

        if payload.get("camera") != camera_name:
            raise InvalidStreamTokenError("Token was issued for a different camera")

        return StreamTokenPayload(
            user_id=int(payload["sub"]),
            camera_name=payload["camera"],
            audio=payload.get("audio") is True,
            session_jti=sid if isinstance(sid := payload.get("sid"), str) else None,
        )
