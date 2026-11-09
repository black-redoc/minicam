"""Tests for mobile push through the central relay (ADR 0011): validation,
payload encryption, the relay client, the token repository and delivery."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import aiosqlite
import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import state
from app.db.connection import apply_migrations
from app.repositories.mobile_push_token_repository import (
    SQLiteMobilePushTokenRepository,
)
from app.repositories.permission_repository import SQLitePermissionRepository
from app.repositories.user_repository import SQLiteUserRepository
from app.services import central as central_module
from app.services.central import CentralRuntime
from app.services.central_config import CentralSettings
from app.services.notifications.events import DetectionEvent, EventHub
from app.services.notifications.mobile_push import (
    MAX_MESSAGES_PER_CALL,
    InvalidMobileTokenError,
    MobilePushRelay,
    RelayError,
    RelayMessage,
    encrypt_payload,
    validate_key,
    validate_platform,
    validate_token,
)
from app.services.notifications.service import NotificationService
from app.services.notifications.web_push import b64url_decode, b64url_encode

URL = "https://admin.example.com"
KEY = b64url_encode(bytes(range(32)))
_EVENT = DetectionEvent("cam1", ("person",), datetime(2026, 11, 9, tzinfo=UTC))


def _relay(handler: httpx.MockTransport) -> tuple[MobilePushRelay, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler)
    return MobilePushRelay(http, URL, "mcd_secret"), http


def _decrypt(key: str, data: dict[str, str]) -> dict[str, object]:
    assert data["v"] == "1"
    plain = AESGCM(b64url_decode(key)).decrypt(
        b64url_decode(data["iv"]), b64url_decode(data["ct"]), None
    )
    return json.loads(plain)  # type: ignore[no-any-return]


def test_validation_accepts_only_tokens_keys_and_platforms_it_knows() -> None:
    validate_token("fcm:abc-1_2")
    validate_platform("android")
    assert validate_key(KEY) == bytes(range(32))
    for bad_token in ("", "has space", "x" * 4097, "é"):
        with pytest.raises(InvalidMobileTokenError):
            validate_token(bad_token)
    for bad_key in ("", b64url_encode(b"short"), "!!!!", KEY + "AA"):
        with pytest.raises(InvalidMobileTokenError):
            validate_key(bad_key)
    with pytest.raises(InvalidMobileTokenError):
        validate_platform("ios")


def test_encrypt_payload_roundtrip() -> None:
    data = encrypt_payload(KEY, b'{"camera": "cam1"}')
    assert _decrypt(KEY, data) == {"camera": "cam1"}
    # A fresh IV every time: two messages never share a nonce.
    assert encrypt_payload(KEY, b"x")["iv"] != encrypt_payload(KEY, b"x")["iv"]
    with pytest.raises(Exception):  # noqa: B017 - any AES-GCM failure is a rejection
        _decrypt(b64url_encode(bytes(32)), data)


async def test_relay_client_talks_to_the_dashboard() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"available": True})
        if request.url.path == "/api/device/push-tokens":
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        tokens = [m["token"] for m in body["messages"]]
        return httpx.Response(
            200,
            json={
                "sent": len(tokens),
                "gone": [t for t in tokens if t.startswith("gone")],
                "unknown": [t for t in tokens if t.startswith("lost")],
            },
        )

    relay, http = _relay(httpx.MockTransport(handle))
    async with http:
        assert await relay.available()
        await relay.register("ana@example.com", "android", "tok")
        await relay.unregister("tok")
        messages = [RelayMessage(f"ok{i}", {"v": "1"}) for i in range(41)]
        messages += [
            RelayMessage("gone1", {"v": "1"}),
            RelayMessage("lost1", {"v": "1"}),
        ]
        outcome = await relay.send(messages)

    assert outcome.gone == ["gone1"] and outcome.unknown == ["lost1"]
    assert all(r.headers["authorization"] == "Bearer mcd_secret" for r in requests)
    assert json.loads(requests[1].content) == {
        "minicamUser": "ana@example.com",
        "platform": "android",
        "token": "tok",
    }
    assert (requests[2].method, json.loads(requests[2].content)) == (
        "DELETE",
        {"token": "tok"},
    )
    pushes = [json.loads(r.content) for r in requests[3:]]
    assert [len(p["messages"]) for p in pushes] == [MAX_MESSAGES_PER_CALL, 3]


async def test_relay_client_failures_never_escape_sending() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    relay, http = _relay(httpx.MockTransport(down))
    async with http:
        assert not await relay.available()
        with pytest.raises(RelayError):
            await relay.register("ana@example.com", "android", "tok")
        await relay.unregister("tok")
        assert (await relay.send([RelayMessage("t", {"v": "1"})])).gone == []

    refusing = httpx.MockTransport(lambda _: httpx.Response(503, json={"error": "x"}))
    relay, http = _relay(refusing)
    async with http:
        assert not await relay.available()
        with pytest.raises(RelayError):
            await relay.register("ana@example.com", "android", "tok")
        await relay.unregister("tok")
        assert (await relay.send([RelayMessage("t", {"v": "1"})])).unknown == []

    garbled = httpx.MockTransport(lambda _: httpx.Response(200, content=b"not json"))
    relay, http = _relay(garbled)
    async with http:
        assert not await relay.available()
        assert (await relay.send([RelayMessage("t", {"v": "1"})])).gone == []


async def test_token_repository_moves_tokens_and_drops_per_session(
    db_connection: aiosqlite.Connection,
) -> None:
    users = SQLiteUserRepository(db_connection)
    ana = await users.create("ana@example.com", "h", is_admin=False)
    beto = await users.create("beto@example.com", "h", is_admin=False)
    repo = SQLiteMobilePushTokenRepository(db_connection)

    await repo.upsert(ana.id, "jti-a", "android", "phone", KEY)
    await repo.upsert(beto.id, "jti-b", "android", "phone", KEY)
    await repo.upsert(ana.id, "jti-a", "android", "tablet", KEY)
    assert [t.email for t in await repo.list_for_users([ana.id, beto.id])] == [
        "beto@example.com",
        "ana@example.com",
    ]
    assert await repo.list_for_users([]) == []

    assert not await repo.delete(ana.id, "phone")  # beto owns it now
    assert await repo.delete_for_session("jti-a") == ["tablet"]
    await repo.delete_tokens(["phone"])
    assert await repo.list_for_users([ana.id, beto.id]) == []


class _FakeRelay:
    def __init__(
        self, gone: tuple[str, ...] = (), unknown: tuple[str, ...] = ()
    ) -> None:
        self.sent: list[RelayMessage] = []
        self.registered: list[tuple[str, str, str]] = []
        self._gone, self._unknown = gone, unknown

    async def send(self, messages: list[RelayMessage]) -> object:
        self.sent += messages
        return type(
            "Outcome", (), {"gone": list(self._gone), "unknown": list(self._unknown)}
        )

    async def register(self, email: str, platform: str, token: str) -> None:
        self.registered.append((email, platform, token))
        if token == "lost-fails":
            raise RelayError("HTTP 503")


async def _seed(conn: aiosqlite.Connection) -> SQLiteMobilePushTokenRepository:
    users = SQLiteUserRepository(conn)
    viewer = await users.create("viewer@example.com", "h", is_admin=False)
    outsider = await users.create("outsider@example.com", "h", is_admin=False)
    await SQLitePermissionRepository(conn).upsert(
        viewer.id, "cam1", can_view=True, can_use_ptz=False
    )
    tokens = SQLiteMobilePushTokenRepository(conn)
    for token in ("phone", "gone", "lost", "lost-fails"):
        await tokens.upsert(viewer.id, "jti", "android", token, KEY)
    await tokens.upsert(outsider.id, "jti-o", "android", "outsider", KEY)
    return tokens


async def test_mobile_push_sends_encrypted_events(
    db_connection: aiosqlite.Connection,
) -> None:
    await _seed(db_connection)
    relay = _FakeRelay()
    service = NotificationService(
        EventHub(), connection=lambda: db_connection, mobile_relay=lambda: relay
    )
    service.handle(_EVENT)
    await service.aclose()

    assert sorted(m.token for m in relay.sent) == [
        "gone",
        "lost",
        "lost-fails",
        "phone",
    ]
    payload = _decrypt(KEY, relay.sent[0].data)
    assert payload["camera"] == "cam1" and payload["classes"] == ["person"]
    assert "phone" not in json.dumps(relay.sent[0].data)


async def test_mobile_push_handles_gone_and_unknown(
    db_connection: aiosqlite.Connection,
) -> None:
    tokens = await _seed(db_connection)
    relay = _FakeRelay(gone=("gone",), unknown=("lost", "lost-fails"))
    service = NotificationService(
        EventHub(), connection=lambda: db_connection, mobile_relay=lambda: relay
    )
    service.handle(_EVENT)
    await service.aclose()

    remaining = {t.token for t in await tokens.list_for_users([1, 2])}
    assert remaining == {"phone", "lost", "lost-fails", "outsider"}
    assert relay.registered == [
        ("viewer@example.com", "android", "lost"),
        ("viewer@example.com", "android", "lost-fails"),
    ]


async def test_mobile_push_failure_is_logged(
    db_connection: aiosqlite.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(db_connection)

    class _Broken(_FakeRelay):
        async def send(self, messages: list[RelayMessage]) -> object:
            raise RuntimeError("boom")

    service = NotificationService(
        EventHub(), connection=lambda: db_connection, mobile_relay=lambda: _Broken()
    )
    service.handle(_EVENT)
    await service.aclose()
    assert "Mobile push delivery failed" in caplog.text


async def test_mobile_push_skips_cameras_without_phones(
    db_connection: aiosqlite.Connection,
) -> None:
    relay = _FakeRelay()
    service = NotificationService(
        EventHub(), connection=lambda: db_connection, mobile_relay=lambda: relay
    )
    service.handle(_EVENT)
    await service.aclose()
    assert relay.sent == []


async def test_central_runtime_sets_mobile_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def idle(_: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(central_module, "run_sync_loop", idle)
    monkeypatch.setattr(central_module, "run_usage_loop", idle)
    conn = await aiosqlite.connect(":memory:")
    await apply_migrations(conn)
    runtime = CentralRuntime(conn)
    try:
        await runtime.start(CentralSettings(URL, "mcd_a"))
        assert isinstance(state.mobile_push_relay, MobilePushRelay)
        await runtime.stop()
        assert state.mobile_push_relay is None
    finally:
        await runtime.stop()
        await conn.close()
