"""Web Push delivery: RFC 8291 payload encryption and RFC 8292 VAPID auth.

Built on cryptography + pyjwt (already dependencies for the security layer)
instead of pywebpush, which would pull requests, http-ece and py-vapid onto
the device for the same ~100 lines of well-specified crypto. The encryption
is pinned against the RFC 8291 Appendix A test vector in the unit tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_RECORD_SIZE = 4096
_SALT_BYTES = 16
_AUTH_SECRET_BYTES = 16
_UNCOMPRESSED_P256_BYTES = 65
_VAPID_TOKEN_TTL_SECONDS = 12 * 60 * 60
_PUSH_TTL_SECONDS = 60 * 60
_SEND_TIMEOUT_SECONDS = 10.0

# Hosts of the browser vendors' push services. The server POSTs to whatever
# endpoint a browser registered, so only these are accepted — otherwise a
# logged-in user could make the device send requests to arbitrary (e.g. LAN)
# URLs.
_PUSH_SERVICE_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


class InvalidSubscriptionError(ValueError):
    """The subscription endpoint or keys are not usable for Web Push."""


class PushResult(Enum):
    DELIVERED = "delivered"
    GONE = "gone"  # the push service says the subscription no longer exists
    FAILED = "failed"


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def validate_endpoint(endpoint: str) -> None:
    """Accept only https endpoints on a known browser push service."""
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(
        host == suffix or host.endswith("." + suffix)
        for suffix in _PUSH_SERVICE_HOST_SUFFIXES
    ):
        raise InvalidSubscriptionError("Unsupported push service endpoint")


def validate_keys(p256dh: str, auth: str) -> None:
    """Check the browser's keys decode to a P-256 point and a 16-byte secret."""
    try:
        public_key = b64url_decode(p256dh)
        auth_secret = b64url_decode(auth)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    except ValueError as exc:
        raise InvalidSubscriptionError("Invalid subscription keys") from exc
    if (
        len(public_key) != _UNCOMPRESSED_P256_BYTES
        or len(auth_secret) != _AUTH_SECRET_BYTES
    ):
        raise InvalidSubscriptionError("Invalid subscription keys")


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA-256 with a single expand block (length <= 32)."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def encrypt_payload(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    sender_private_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """Encrypt one push message with the aes128gcm content coding (RFC 8291).

    sender_private_key and salt are fresh random values in production; they
    are parameters only so tests can reproduce the RFC's test vector.
    """
    as_private = sender_private_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_private.public_key())
    salt = salt or os.urandom(_SALT_BYTES)

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hkdf(auth_secret, ecdh_secret, key_info, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    # A single record: the payload plus the 0x02 last-record delimiter.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = (
        salt
        + _RECORD_SIZE.to_bytes(4, "big")
        + len(as_public).to_bytes(1, "big")
        + as_public
    )
    return header + ciphertext


@dataclass(frozen=True, slots=True)
class VapidKey:
    """The device's VAPID identity: an ES256 key plus a contact subject."""

    private_key: ec.EllipticCurvePrivateKey
    subject: str

    @classmethod
    def generate_pem(cls) -> str:
        key = ec.generate_private_key(ec.SECP256R1())
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")

    @classmethod
    def from_pem(cls, pem: str, subject: str) -> VapidKey:
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("VAPID key must be an EC P-256 private key")
        return cls(private_key=key, subject=subject)

    @property
    def public_key_b64url(self) -> str:
        """The applicationServerKey the browser subscribes with."""
        return b64url_encode(_public_bytes(self.private_key.public_key()))

    def authorization_header(self, endpoint: str, now: float) -> str:
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int(now) + _VAPID_TOKEN_TTL_SECONDS,
            "sub": self.subject,
        }
        token = jwt.encode(claims, self.private_key, algorithm="ES256")
        return f"vapid t={token}, k={self.public_key_b64url}"


class WebPushSender:
    """Sends encrypted payloads to browser push services."""

    def __init__(
        self,
        vapid_key: VapidKey,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._vapid_key = vapid_key
        self._client = client or httpx.AsyncClient(timeout=_SEND_TIMEOUT_SECONDS)

    @property
    def public_key(self) -> str:
        return self._vapid_key.public_key_b64url

    async def send(
        self, endpoint: str, p256dh: str, auth: str, payload: bytes
    ) -> PushResult:
        body = encrypt_payload(payload, b64url_decode(p256dh), b64url_decode(auth))
        headers = {
            "Authorization": self._vapid_key.authorization_header(
                endpoint, time.time()
            ),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(_PUSH_TTL_SECONDS),
            "Urgency": "high",
        }
        try:
            response = await self._client.post(endpoint, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Web Push request failed: %s", exc)
            return PushResult.FAILED
        if response.status_code in (404, 410):
            return PushResult.GONE
        if response.is_success:
            return PushResult.DELIVERED
        logger.warning(
            "Web Push rejected by push service: status=%d", response.status_code
        )
        return PushResult.FAILED

    async def aclose(self) -> None:
        await self._client.aclose()
