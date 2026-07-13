"""Password hashing and session-token (login JWT) issuance/verification."""

from __future__ import annotations

import time
from dataclasses import dataclass

import bcrypt
import jwt

_SESSION_TOKEN_TYPE = "session"


class InvalidSessionTokenError(Exception):
    """Raised when a session token is missing, malformed, expired, or forged."""


class PasswordHasher:
    """Wraps bcrypt so callers never touch salts or encodings directly."""

    def hash(self, plain_password: str) -> str:
        return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

    def verify(self, plain_password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), password_hash.encode("utf-8")
        )


@dataclass(frozen=True, slots=True)
class SessionTokenPayload:
    user_id: int
    jti: str


class TokenService:
    """Issues and verifies the session JWT (login). Revocation is enforced
    separately by checking the sessions table, not by this class.
    """

    def __init__(self, secret: str, algorithm: str = "HS256") -> None:
        self._secret = secret
        self._algorithm = algorithm

    def issue(self, user_id: int, jti: str, ttl_seconds: int) -> str:
        now = int(time.time())
        payload = {
            "sub": str(user_id),
            "jti": jti,
            "typ": _SESSION_TOKEN_TYPE,
            "iat": now,
            "exp": now + ttl_seconds,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def verify(self, token: str) -> SessionTokenPayload:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.PyJWTError as exc:
            raise InvalidSessionTokenError(
                "Malformed or expired session token"
            ) from exc

        if payload.get("typ") != _SESSION_TOKEN_TYPE:
            raise InvalidSessionTokenError("Token is not a session token")

        return SessionTokenPayload(user_id=int(payload["sub"]), jti=payload["jti"])
