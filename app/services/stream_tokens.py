"""Short-lived, stateless tokens that authorize a single WebSocket handshake.

Distinct from the session JWT (TokenService) on purpose: a stream token is
bound to one camera via the `camera` claim (and, with the `audio` claim,
lets the socket carry that camera's audio), carries a short TTL (~30s, see
security_settings.stream_token_ttl_seconds), and names the session it was
issued for (`sid`). Each token is single-use: its `jti` is remembered in
memory (UsedStreamTokens) until it expires, so a token copied from a log or a
URL cannot open a second socket. Once the handshake succeeds the socket stays
open even if the token has since expired; the stream router re-checks that
session and the viewer's permissions while it plays (see
app/services/stream_access.py).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt

_STREAM_TOKEN_TYPE = "stream"


class InvalidStreamTokenError(Exception):
    """Raised when a stream token is missing, malformed, expired, of the
    wrong type (e.g. a session token reused as a stream token), issued for
    a different camera than the one being requested, or already used.
    """


@dataclass(frozen=True, slots=True)
class StreamTokenPayload:
    user_id: int
    camera_name: str
    audio: bool = False
    session_jti: str | None = None


class UsedStreamTokens:
    """In-memory set of the stream token `jti`s already used, each kept until
    its token expires (after that the signature check rejects it anyway).

    One uvicorn process serves every request, so a per-process set is enough;
    a restart forgets it, which only reopens tokens younger than their TTL.
    Not locked: claim() never awaits, so it runs atomically in the event loop.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._expiries: dict[str, float] = {}

    def claim(self, jti: str, expires_at: float) -> bool:
        """Mark jti as used; False if it already was."""
        now = self._clock()
        self._expiries = {k: exp for k, exp in self._expiries.items() if exp > now}
        if jti in self._expiries:
            return False
        self._expiries[jti] = expires_at
        return True


class StreamTokenService:
    def __init__(
        self,
        secret: str,
        algorithm: str = "HS256",
        used_tokens: UsedStreamTokens | None = None,
    ) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._used_tokens = (
            used_tokens if used_tokens is not None else UsedStreamTokens()
        )

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
            "jti": uuid.uuid4().hex,
        }
        if audio:
            payload["audio"] = True
        if session_jti is not None:
            payload["sid"] = session_jti
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def verify(self, token: str, camera_name: str) -> StreamTokenPayload:
        """Check the token and consume it: a second verify of it fails."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["exp", "jti"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidStreamTokenError("Malformed or expired stream token") from exc

        if payload.get("typ") != _STREAM_TOKEN_TYPE:
            raise InvalidStreamTokenError("Token is not a stream token")

        if payload.get("camera") != camera_name:
            raise InvalidStreamTokenError("Token was issued for a different camera")

        jti = payload["jti"]
        if not isinstance(jti, str) or not jti:
            raise InvalidStreamTokenError("Token has no valid jti")
        if not self._used_tokens.claim(jti, float(payload["exp"])):
            raise InvalidStreamTokenError("Token was already used")

        return StreamTokenPayload(
            user_id=int(payload["sub"]),
            camera_name=payload["camera"],
            audio=payload.get("audio") is True,
            session_jti=sid if isinstance(sid := payload.get("sid"), str) else None,
        )
