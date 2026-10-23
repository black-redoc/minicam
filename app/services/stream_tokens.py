"""Short-lived, stateless tokens that authorize a single WebSocket handshake.

Distinct from the session JWT (TokenService) on purpose: a stream token is
bound to one camera via the `camera` claim (and, with the `audio` claim,
lets the socket carry that camera's audio), carries a short TTL (~30s, see
security_settings.stream_token_ttl_seconds), and is never checked against the
sessions table — once the handshake succeeds the socket stays open even if
the token has since expired, the same trust model as a signed S3 URL.
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


class StreamTokenService:
    def __init__(self, secret: str, algorithm: str = "HS256") -> None:
        self._secret = secret
        self._algorithm = algorithm

    def issue(
        self, user_id: int, camera_name: str, ttl_seconds: int, audio: bool = False
    ) -> str:
        """audio=True authorizes the camera's audio: only for can_listen."""
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
        )
