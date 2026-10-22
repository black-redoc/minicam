"""Tests for Web Push encryption (RFC 8291), VAPID (RFC 8292) and sending."""

from __future__ import annotations

import hashlib
import hmac

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.notifications.web_push import (
    InvalidSubscriptionError,
    PushResult,
    VapidKey,
    WebPushSender,
    b64url_decode,
    b64url_encode,
    encrypt_payload,
    validate_endpoint,
    validate_keys,
)

# RFC 8291 Appendix A.
_RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
_RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
_RFC_UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpk"
    "NtoIAiw4"
)
_RFC_AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"
_RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
_RFC_MESSAGE = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3"
    "vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuw"
    "XPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)
_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _ua_keys() -> tuple[ec.EllipticCurvePrivateKey, str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return private, b64url_encode(public), b64url_encode(b"0123456789abcdef")


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """Receiver side of RFC 8291, as a browser would run it."""
    salt, key_len = body[:16], body[20]
    as_public = body[21 : 21 + key_len]
    ciphertext = body[21 + key_len :]
    ua_public = ua_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = ua_private.exchange(
        ec.ECDH(),
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public),
    )
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert padded.endswith(b"\x02")
    return padded[:-1]


class TestEncryptPayload:
    def test_matches_rfc_8291_test_vector(self) -> None:
        sender = ec.derive_private_key(
            int.from_bytes(b64url_decode(_RFC_AS_PRIVATE), "big"), ec.SECP256R1()
        )
        body = encrypt_payload(
            _RFC_PLAINTEXT,
            b64url_decode(_RFC_UA_PUBLIC),
            b64url_decode(_RFC_AUTH_SECRET),
            sender_private_key=sender,
            salt=b64url_decode(_RFC_SALT),
        )
        assert b64url_encode(body) == _RFC_MESSAGE

    def test_round_trips_with_fresh_keys(self) -> None:
        ua_private, ua_public, auth = _ua_keys()
        body = encrypt_payload(
            b'{"body": "person"}', b64url_decode(ua_public), b64url_decode(auth)
        )
        assert _decrypt(body, ua_private, b64url_decode(auth)) == b'{"body": "person"}'

    def test_uses_fresh_salt_and_key_per_message(self) -> None:
        _, ua_public, auth = _ua_keys()
        first = encrypt_payload(b"x", b64url_decode(ua_public), b64url_decode(auth))
        second = encrypt_payload(b"x", b64url_decode(ua_public), b64url_decode(auth))
        assert first[:16] != second[:16]
        assert first[21:86] != second[21:86]


class TestValidateEndpoint:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://fcm.googleapis.com/fcm/send/x",
            "https://updates.push.services.mozilla.com/wpush/v2/x",
            "https://web.push.apple.com/x",
            "https://wns2-par02p.notify.windows.com/w/?token=x",
        ],
    )
    def test_accepts_browser_push_services(self, endpoint: str) -> None:
        validate_endpoint(endpoint)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://fcm.googleapis.com/fcm/send/x",
            "https://192.168.1.1/x",
            "https://evilfcm.googleapis.com.attacker.net/x",
            "https://notfcm.googleapis.com.evil/x",
            "https://attacker.net/fcm.googleapis.com",
            "not a url",
        ],
    )
    def test_rejects_other_hosts_and_schemes(self, endpoint: str) -> None:
        with pytest.raises(InvalidSubscriptionError):
            validate_endpoint(endpoint)


class TestValidateKeys:
    def test_accepts_valid_keys(self) -> None:
        _, p256dh, auth = _ua_keys()
        validate_keys(p256dh, auth)

    @pytest.mark.parametrize(
        ("p256dh", "auth"),
        [
            ("BAAA", b64url_encode(b"0123456789abcdef")),
            (_RFC_UA_PUBLIC, b64url_encode(b"short")),
            ("!!!", "!!!"),
        ],
    )
    def test_rejects_invalid_keys(self, p256dh: str, auth: str) -> None:
        with pytest.raises(InvalidSubscriptionError):
            validate_keys(p256dh, auth)


class TestVapidKey:
    def test_pem_round_trip_keeps_public_key(self) -> None:
        pem = VapidKey.generate_pem()
        first = VapidKey.from_pem(pem, "mailto:a@example.com")
        second = VapidKey.from_pem(pem, "mailto:a@example.com")
        assert first.public_key_b64url == second.public_key_b64url
        assert len(b64url_decode(first.public_key_b64url)) == 65

    def test_rejects_non_ec_key(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ed25519

        pem = (
            ed25519.Ed25519PrivateKey.generate()
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        with pytest.raises(ValueError, match="EC P-256"):
            VapidKey.from_pem(pem, "mailto:a@example.com")

    def test_authorization_header_is_signed_vapid_jwt(self) -> None:
        key = VapidKey.from_pem(VapidKey.generate_pem(), "mailto:a@example.com")
        header = key.authorization_header(_ENDPOINT, now=1_000_000)

        token = header.removeprefix("vapid t=").split(", k=")[0]
        assert header.endswith(f", k={key.public_key_b64url}")
        claims = jwt.decode(
            token,
            key.private_key.public_key(),
            algorithms=["ES256"],
            audience="https://fcm.googleapis.com",
            options={"verify_exp": False},
        )
        assert claims["sub"] == "mailto:a@example.com"
        assert claims["exp"] == 1_000_000 + 12 * 60 * 60


def _sender(handler: httpx.MockTransport) -> WebPushSender:
    key = VapidKey.from_pem(VapidKey.generate_pem(), "mailto:a@example.com")
    return WebPushSender(key, client=httpx.AsyncClient(transport=handler))


class TestWebPushSender:
    async def test_posts_encrypted_payload_with_headers(self) -> None:
        ua_private, p256dh, auth = _ua_keys()
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201)

        sender = _sender(httpx.MockTransport(handler))
        result = await sender.send(_ENDPOINT, p256dh, auth, b"hello")

        assert result is PushResult.DELIVERED
        request = requests[0]
        assert str(request.url) == _ENDPOINT
        assert request.headers["Content-Encoding"] == "aes128gcm"
        assert request.headers["TTL"] == "3600"
        assert request.headers["Authorization"].startswith("vapid t=")
        assert _decrypt(request.content, ua_private, b64url_decode(auth)) == b"hello"
        await sender.aclose()

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(404, PushResult.GONE), (410, PushResult.GONE), (500, PushResult.FAILED)],
    )
    async def test_maps_push_service_status(
        self, status: int, expected: PushResult
    ) -> None:
        _, p256dh, auth = _ua_keys()
        sender = _sender(httpx.MockTransport(lambda _: httpx.Response(status)))
        assert await sender.send(_ENDPOINT, p256dh, auth, b"x") is expected

    async def test_network_error_is_failed(self) -> None:
        _, p256dh, auth = _ua_keys()

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        sender = _sender(httpx.MockTransport(handler))
        assert await sender.send(_ENDPOINT, p256dh, auth, b"x") is PushResult.FAILED

    def test_exposes_public_key(self) -> None:
        sender = _sender(httpx.MockTransport(lambda _: httpx.Response(201)))
        assert len(b64url_decode(sender.public_key)) == 65
