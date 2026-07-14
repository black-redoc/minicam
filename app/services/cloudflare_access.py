"""Verifies Cloudflare Access JWTs (the `Cf-Access-Jwt-Assertion` header).

Access already gates the tunnel at the network layer, but that alone means
FastAPI trusts "the request arrived" as a proxy for "Access approved it" —
this module closes that gap by cryptographically checking the JWT itself
against Cloudflare's published JWKS and returning the verified identity.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt

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
            raise InvalidAccessTokenError(
                "Malformed, expired, or forged Cloudflare Access token"
            ) from exc

        email = payload.get("email")
        if not email:
            raise InvalidAccessTokenError("Access token is missing the email claim")

        return AccessTokenPayload(email=email)
