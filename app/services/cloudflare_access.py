"""Verifies Cloudflare Access JWTs (the `Cf-Access-Jwt-Assertion` header).

Access already gates the tunnel at the network layer, but that alone means
FastAPI trusts "the request arrived" as a proxy for "Access approved it" —
this module closes that gap by cryptographically checking the JWT itself
against Cloudflare's published JWKS and returning the verified identity.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import jwt

logger = logging.getLogger(__name__)

_ACCESS_JWT_ALGORITHM = "RS256"


class InvalidAccessTokenError(Exception):
    """Raised when a Cloudflare Access JWT is missing, malformed, expired,
    forged, or issued for a different application/team.
    """


@dataclass(frozen=True, slots=True)
class AccessTokenPayload:
    email: str


class CloudflareAccessVerifier:
    """Verifies a Cf-Access-Jwt-Assertion token and extracts the user email.

    The JWKS client is injected rather than constructed here so tests can
    supply a fake signing key instead of reaching the real
    `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` endpoint.
    """

    def __init__(
        self, jwks_client: jwt.PyJWKClient, audience: str, issuer: str
    ) -> None:
        self._jwks_client = jwks_client
        self._audience = audience
        self._issuer = issuer

    def verify(self, token: str) -> AccessTokenPayload:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[_ACCESS_JWT_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError as exc:
            logger.warning(
                "Rejected Cloudflare Access token: %s: %s (%s)",
                type(exc).__name__,
                exc,
                self._describe_unverified(token),
            )
            raise InvalidAccessTokenError(
                "Malformed, expired, or forged Cloudflare Access token"
            ) from exc

        email = payload.get("email")
        if not email:
            logger.warning(
                "Rejected Cloudflare Access token: no email claim (%s)",
                self._describe_unverified(token),
            )
            raise InvalidAccessTokenError("Access token is missing the email claim")

        return AccessTokenPayload(email=email)

    def _describe_unverified(self, token: str) -> str:
        """Summarize a rejected token's non-identifying claims for the log.

        Never includes the token itself or the email: only what's needed to
        tell an expired token from a wrong application (aud) or team (iss).
        The claims are read without verification, so they are only a hint.
        """
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return "token is not a decodable JWT"
        now = int(time.time())
        parts = [
            f"aud={claims.get('aud')!r} expected={self._audience!r}",
            f"iss={claims.get('iss')!r} expected={self._issuer!r}",
        ]
        for claim in ("iat", "exp"):
            value = claims.get(claim)
            if isinstance(value, int | float):
                parts.append(f"{claim}={int(value) - now:+d}s from now")
        return ", ".join(parts)
