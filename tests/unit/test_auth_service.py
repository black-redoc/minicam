"""Tests for PasswordHasher and TokenService (session JWT)."""

from __future__ import annotations

import time

import jwt
import pytest

from app.services.auth import InvalidSessionTokenError, PasswordHasher, TokenService


class TestPasswordHasher:
    def test_verify_accepts_correct_password(self) -> None:
        hasher = PasswordHasher()
        password_hash = hasher.hash("correct horse battery staple")
        assert hasher.verify("correct horse battery staple", password_hash) is True

    def test_verify_rejects_wrong_password(self) -> None:
        hasher = PasswordHasher()
        password_hash = hasher.hash("correct horse battery staple")
        assert hasher.verify("wrong password", password_hash) is False

    def test_hash_is_not_the_plain_password(self) -> None:
        hasher = PasswordHasher()
        assert hasher.hash("secret") != "secret"

    def test_hash_is_salted_and_never_repeats(self) -> None:
        hasher = PasswordHasher()
        assert hasher.hash("secret") != hasher.hash("secret")


class TestTokenServiceIssueAndVerify:
    def test_verify_returns_matching_user_id(self) -> None:
        service = TokenService(secret="test-secret")
        token = service.issue(user_id=42, jti="jti-1", ttl_seconds=60)
        payload = service.verify(token)
        assert payload.user_id == 42
        assert payload.jti == "jti-1"

    def test_verify_rejects_token_signed_with_different_secret(self) -> None:
        issuer = TokenService(secret="secret-a")
        verifier = TokenService(secret="secret-b")
        token = issuer.issue(user_id=1, jti="jti-1", ttl_seconds=60)
        with pytest.raises(InvalidSessionTokenError):
            verifier.verify(token)

    def test_verify_rejects_expired_token(self) -> None:
        service = TokenService(secret="test-secret")
        token = service.issue(user_id=1, jti="jti-1", ttl_seconds=-1)
        with pytest.raises(InvalidSessionTokenError):
            service.verify(token)

    def test_verify_rejects_malformed_token(self) -> None:
        service = TokenService(secret="test-secret")
        with pytest.raises(InvalidSessionTokenError):
            service.verify("not-a-jwt")

    def test_verify_rejects_token_missing_session_type_claim(self) -> None:
        service = TokenService(secret="test-secret")
        now = int(time.time())
        forged = jwt.encode(
            {"sub": "1", "jti": "jti-1", "iat": now, "exp": now + 60},
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(InvalidSessionTokenError):
            service.verify(forged)
