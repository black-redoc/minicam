"""Tests for StreamTokenService: short-lived, camera-bound WebSocket tokens."""

from __future__ import annotations

import time

import jwt
import pytest

from app.services.auth import TokenService
from app.services.stream_tokens import InvalidStreamTokenError, StreamTokenService


class TestStreamTokenIssueAndVerify:
    def test_session_jti_round_trips(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(7, "cam1", ttl_seconds=30, session_jti="sid-1")
        assert service.verify(token, camera_name="cam1").session_jti == "sid-1"

    def test_token_without_session_has_no_session_jti(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(7, "cam1", ttl_seconds=30)
        assert service.verify(token, camera_name="cam1").session_jti is None

    def test_non_string_session_claim_is_ignored(self) -> None:
        token = jwt.encode(
            {
                "sub": "7",
                "camera": "cam1",
                "typ": "stream",
                "sid": 123,
                "exp": int(time.time()) + 30,
            },
            "test-secret",
            algorithm="HS256",
        )
        payload = StreamTokenService(secret="test-secret").verify(token, "cam1")
        assert payload.session_jti is None

    def test_verify_returns_matching_payload(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(user_id=7, camera_name="cam1", ttl_seconds=30)
        payload = service.verify(token, camera_name="cam1")
        assert payload.user_id == 7
        assert payload.camera_name == "cam1"

    def test_verify_rejects_token_for_a_different_camera(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(user_id=7, camera_name="cam1", ttl_seconds=30)
        with pytest.raises(InvalidStreamTokenError):
            service.verify(token, camera_name="cam2")

    def test_verify_rejects_expired_token(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(user_id=7, camera_name="cam1", ttl_seconds=-1)
        with pytest.raises(InvalidStreamTokenError):
            service.verify(token, camera_name="cam1")

    def test_verify_rejects_malformed_token(self) -> None:
        service = StreamTokenService(secret="test-secret")
        with pytest.raises(InvalidStreamTokenError):
            service.verify("not-a-jwt", camera_name="cam1")

    def test_verify_rejects_a_session_token_used_as_stream_token(self) -> None:
        session_service = TokenService(secret="test-secret")
        stream_service = StreamTokenService(secret="test-secret")
        session_token = session_service.issue(user_id=7, jti="jti-1", ttl_seconds=60)
        with pytest.raises(InvalidStreamTokenError):
            stream_service.verify(session_token, camera_name="cam1")

    def test_verify_rejects_token_signed_with_different_secret(self) -> None:
        issuer = StreamTokenService(secret="secret-a")
        verifier = StreamTokenService(secret="secret-b")
        token = issuer.issue(user_id=7, camera_name="cam1", ttl_seconds=30)
        with pytest.raises(InvalidStreamTokenError):
            verifier.verify(token, camera_name="cam1")


class TestStreamTokenAudioClaim:
    def test_tokens_carry_no_audio_by_default(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(user_id=7, camera_name="cam1", ttl_seconds=30)
        assert service.verify(token, camera_name="cam1").audio is False

    def test_audio_claim_round_trips(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = service.issue(user_id=7, camera_name="cam1", ttl_seconds=30, audio=True)
        assert service.verify(token, camera_name="cam1").audio is True

    def test_only_a_true_audio_claim_counts(self) -> None:
        service = StreamTokenService(secret="test-secret")
        token = jwt.encode(
            {
                "sub": "7",
                "camera": "cam1",
                "typ": "stream",
                "audio": "yes",
                "exp": int(time.time()) + 30,
            },
            "test-secret",
            algorithm="HS256",
        )
        assert service.verify(token, camera_name="cam1").audio is False
