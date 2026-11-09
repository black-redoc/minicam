"""Detection pushes to the mobile apps through the central relay (ADR 0011).

The Firebase key lives only in minicam-admin (its ADR 0013). minicam
registers each phone's FCM token there and hands it end-to-end encrypted
data messages: the event JSON under the phone's own AES-256 key, so neither
the relay nor Google can read camera names or classes. Tokens and keys are
never logged.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.entitlements import REQUEST_TIMEOUT_SECONDS
from app.services.notifications.web_push import b64url_decode, b64url_encode

logger = logging.getLogger(__name__)

PUSH_TOKENS_PATH = "/api/device/push-tokens"
PUSH_PATH = "/api/device/push"
PLATFORMS = ("android",)
PAYLOAD_VERSION = "1"
# The relay's per-call limit.
MAX_MESSAGES_PER_CALL = 40
_KEY_BYTES = 32
_IV_BYTES = 12
# FCM registration tokens are long opaque ASCII strings; nothing else is accepted.
_TOKEN = re.compile(r"[\x21-\x7e]{1,4096}")


class InvalidMobileTokenError(ValueError):
    """The phone's token, key or platform is not acceptable."""


class RelayError(Exception):
    """The dashboard's push relay refused or could not be reached."""


def validate_token(token: str) -> None:
    if not _TOKEN.fullmatch(token):
        raise InvalidMobileTokenError("Invalid push token")


def validate_key(key: str) -> bytes:
    """The phone's AES-256 key: 32 bytes, base64url."""
    try:
        raw = b64url_decode(key)
    except ValueError as exc:
        raise InvalidMobileTokenError("Invalid push key") from exc
    if len(raw) != _KEY_BYTES or b64url_encode(raw) != key.rstrip("="):
        raise InvalidMobileTokenError("Invalid push key")
    return raw


def validate_platform(platform: str) -> None:
    if platform not in PLATFORMS:
        raise InvalidMobileTokenError("Unsupported platform")


def encrypt_payload(
    key: str, plaintext: bytes, iv: bytes | None = None
) -> dict[str, str]:
    """AES-256-GCM, random 12-byte IV, no AAD; `ct` is ciphertext + tag."""
    nonce = iv if iv is not None else os.urandom(_IV_BYTES)
    ciphertext = AESGCM(validate_key(key)).encrypt(nonce, plaintext, None)
    return {
        "v": PAYLOAD_VERSION,
        "iv": b64url_encode(nonce),
        "ct": b64url_encode(ciphertext),
    }


@dataclass(frozen=True, slots=True)
class RelayMessage:
    token: str = field(repr=False)
    data: dict[str, str] = field(repr=False)


@dataclass(slots=True)
class RelayOutcome:
    gone: list[str] = field(default_factory=list, repr=False)
    unknown: list[str] = field(default_factory=list, repr=False)


class MobilePushRelay:
    """Client for minicam-admin's push relay, with this device's token."""

    def __init__(
        self, http: httpx.AsyncClient, base_url: str, device_token: str
    ) -> None:
        self._http = http
        self._base_url = base_url
        self._headers = {"Authorization": f"Bearer {device_token}"}

    async def _request(
        self, method: str, path: str, body: object | None = None
    ) -> httpx.Response:
        try:
            return await self._http.request(
                method,
                self._base_url + path,
                json=body,
                headers=self._headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise RelayError(type(exc).__name__) from exc

    async def available(self) -> bool:
        """The relay answers and has FCM configured."""
        try:
            response = await self._request("GET", PUSH_PATH)
            return (
                response.status_code == 200 and response.json().get("available") is True
            )
        except RelayError, ValueError, AttributeError:
            return False

    async def register(self, email: str, platform: str, token: str) -> None:
        response = await self._request(
            "POST",
            PUSH_TOKENS_PATH,
            {"minicamUser": email, "platform": platform, "token": token},
        )
        if response.status_code != 200:
            raise RelayError(f"HTTP {response.status_code}")

    async def unregister(self, token: str) -> None:
        """Best-effort: a token the relay keeps is harmless, minicam no longer
        sends to it."""
        try:
            response = await self._request("DELETE", PUSH_TOKENS_PATH, {"token": token})
        except RelayError as exc:
            logger.warning("Mobile push unregister failed: %s", exc)
            return
        if response.status_code != 200:
            logger.warning(
                "Mobile push unregister failed: HTTP %d", response.status_code
            )

    async def send(self, messages: Sequence[RelayMessage]) -> RelayOutcome:
        """Send in chunks; a failed chunk is logged and skipped (no retries)."""
        outcome = RelayOutcome()
        for start in range(0, len(messages), MAX_MESSAGES_PER_CALL):
            chunk = messages[start : start + MAX_MESSAGES_PER_CALL]
            body = {"messages": [{"token": m.token, "data": m.data} for m in chunk]}
            try:
                response = await self._request("POST", PUSH_PATH, body)
            except RelayError as exc:
                logger.warning("Mobile push failed: %s", exc)
                continue
            if response.status_code != 200:
                logger.warning("Mobile push failed: HTTP %d", response.status_code)
                continue
            try:
                result = response.json()
                outcome.gone += [
                    t for t in result.get("gone", []) if isinstance(t, str)
                ]
                outcome.unknown += [
                    t for t in result.get("unknown", []) if isinstance(t, str)
                ]
            except ValueError, AttributeError:
                logger.warning("Mobile push: unreadable relay answer")
        return outcome
