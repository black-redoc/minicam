"""Tests for CloudflareAccessVerifier: JWTs signed with a locally generated
RSA keypair (no real network calls to Cloudflare's JWKS endpoint) via a fake
`jwt.PyJWKClient` that returns the test public key.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.cloudflare_access import (
    CloudflareAccessVerifier,
    InvalidAccessTokenError,
)

_AUDIENCE = "test-aud-tag"
_ISSUER = "https://myteam.cloudflareaccess.com"
_EMAIL = "user@example.com"


def _keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign(
    private_key: rsa.RSAPrivateKey,
    *,
    aud: str = _AUDIENCE,
    iss: str = _ISSUER,
    email: str | None = _EMAIL,
    exp_offset: int = 300,
) -> str:
    now = int(time.time())
    payload = {"aud": aud, "iss": iss, "iat": now, "exp": now + exp_offset}
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, private_key, algorithm="RS256")


def _verifier_for(public_key: rsa.RSAPublicKey) -> CloudflareAccessVerifier:
    jwks_client = MagicMock()
    jwks_client.get_signing_key_from_jwt.return_value = MagicMock(key=public_key)
    return CloudflareAccessVerifier(
        jwks_client=jwks_client, audience=_AUDIENCE, issuer=_ISSUER
    )


class TestVerify:
    def test_returns_email_for_a_valid_token(self) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key)
        verifier = _verifier_for(public_key)

        result = verifier.verify(token)

        assert result.email == _EMAIL

    def test_raises_on_expired_token(self) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, exp_offset=-10)
        verifier = _verifier_for(public_key)

        with pytest.raises(InvalidAccessTokenError):
            verifier.verify(token)

    def test_raises_on_wrong_audience(self) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, aud="some-other-app")
        verifier = _verifier_for(public_key)

        with pytest.raises(InvalidAccessTokenError):
            verifier.verify(token)

    def test_raises_on_wrong_issuer(self) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, iss="https://other-team.cloudflareaccess.com")
        verifier = _verifier_for(public_key)

        with pytest.raises(InvalidAccessTokenError):
            verifier.verify(token)

    def test_raises_on_forged_signature(self) -> None:
        forged_private_key, _ = _keypair()
        _, real_public_key = _keypair()
        token = _sign(forged_private_key)
        verifier = _verifier_for(real_public_key)

        with pytest.raises(InvalidAccessTokenError):
            verifier.verify(token)

    def test_raises_when_email_claim_missing(self) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, email=None)
        verifier = _verifier_for(public_key)

        with pytest.raises(InvalidAccessTokenError):
            verifier.verify(token)


class TestRejectionLogging:
    """A rejected token logs why (to diagnose WAN 403s), never the token or email."""

    def test_expired_token_logs_reason_and_age(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, exp_offset=-120)

        with caplog.at_level("WARNING"), pytest.raises(InvalidAccessTokenError):
            _verifier_for(public_key).verify(token)

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "ExpiredSignatureError" in log
        assert "exp=-" in log
        assert token not in log
        assert _EMAIL not in log

    def test_wrong_audience_logs_both_audiences(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, aud="other-app")

        with caplog.at_level("WARNING"), pytest.raises(InvalidAccessTokenError):
            _verifier_for(public_key).verify(token)

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "InvalidAudienceError" in log
        assert "aud='other-app'" in log
        assert f"expected={_AUDIENCE!r}" in log
        assert token not in log

    def test_missing_email_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        private_key, public_key = _keypair()
        token = _sign(private_key, email=None)

        with caplog.at_level("WARNING"), pytest.raises(InvalidAccessTokenError):
            _verifier_for(public_key).verify(token)

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "no email claim" in log
        assert token not in log

    def test_garbage_token_is_described_without_crashing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        verifier_jwks = MagicMock()
        verifier_jwks.get_signing_key_from_jwt.side_effect = jwt.DecodeError("bad")
        verifier = CloudflareAccessVerifier(
            jwks_client=verifier_jwks, audience=_AUDIENCE, issuer=_ISSUER
        )

        with caplog.at_level("WARNING"), pytest.raises(InvalidAccessTokenError):
            verifier.verify("not-a-jwt")

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "DecodeError" in log
        assert "not a decodable JWT" in log
