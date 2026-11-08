"""Unit tests for pairing with the central dashboard (ADR 0009)."""

from __future__ import annotations

import asyncio
import json
import stat
from collections.abc import Iterator
from pathlib import Path

import aiosqlite
import httpx
import pytest

from app import state
from app.db.connection import apply_migrations
from app.security_settings import get_security_settings
from app.services import central as central_module
from app.services.central import (
    CentralRuntime,
    PairedSite,
    PairingError,
    redeem_pairing_code,
)
from app.services.central_config import (
    CentralSettings,
    normalize_central_url,
    read_central_file,
    remove_central_file,
    write_central_file,
)
from tests.unit.test_tunnel_service import AUD, TEAM, make_token

URL = "https://admin.example.com"


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MINICAM_JWT_SECRET", "unit-test-secret")
    monkeypatch.delenv("MINICAM_CENTRAL_URL", raising=False)
    monkeypatch.delenv("MINICAM_DEVICE_TOKEN", raising=False)
    get_security_settings.cache_clear()
    yield
    get_security_settings.cache_clear()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://admin.example.com", URL),
        (" https://Admin.Example.com/ ", URL),
        ("https://admin.example.com:8443", "https://admin.example.com:8443"),
        ("http://localhost:8787", "http://localhost:8787"),
        ("http://admin.example.com", None),
        ("https://admin.example.com/api", None),
        ("https://admin.example.com/?x=1", None),
        ("https://user@admin.example.com", None),
        ("https://admin.example.com:99999", None),
        ("admin.example.com", None),
        ("", None),
    ],
)
def test_normalize_central_url(raw: str, expected: str | None) -> None:
    assert normalize_central_url(raw) == expected


def test_central_file_round_trip(isolated_central_dir: Path) -> None:
    assert read_central_file() is None
    write_central_file(CentralSettings(URL, "mcd_secret"))

    path = isolated_central_dir / "central.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_central_file() == CentralSettings(URL, "mcd_secret")
    assert "mcd_secret" not in repr(read_central_file())

    remove_central_file()
    remove_central_file()
    assert read_central_file() is None


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"url": "http://evil.com", "device_token": "t"}',
        '{"url": "https://a.b"}',
    ],
)
def test_unreadable_central_file_means_not_paired(
    isolated_central_dir: Path, content: str
) -> None:
    (isolated_central_dir / "central.json").write_text(content)
    assert read_central_file() is None


def test_settings_use_the_paired_dashboard_unless_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_central_file(CentralSettings(URL, "mcd_paired"))
    settings = get_security_settings()
    assert settings.central_url == URL
    assert settings.device_token == "mcd_paired"

    get_security_settings.cache_clear()
    monkeypatch.setenv("MINICAM_CENTRAL_URL", "https://env.example.com")
    monkeypatch.setenv("MINICAM_DEVICE_TOKEN", "mcd_env")
    assert get_security_settings().device_token == "mcd_env"


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_redeem_pairing_code_returns_the_site_and_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "token": "mcd_new",
                "site": {"name": "Casa", "hostname": "cam.example.com"},
            },
        )

    async with _client(httpx.MockTransport(handler)) as http:
        paired = await redeem_pairing_code(http, URL, "ABCD-EFGH")

    assert (paired.name, paired.hostname, paired.device_token) == (
        "Casa",
        "cam.example.com",
        "mcd_new",
    )
    assert str(seen[0].url) == f"{URL}/api/device/pair"
    assert json.loads(seen[0].content) == {"code": "ABCD-EFGH"}
    assert "mcd_new" not in repr(paired)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(401, json={"error": "invalid_code"}),
            "did not accept the code",
        ),
        (httpx.Response(500), "answered 500"),
        (httpx.Response(200, text="not json"), "unexpected answer"),
        (
            httpx.Response(
                200, json={"token": "", "site": {"name": "a", "hostname": "b"}}
            ),
            "unexpected",
        ),
        (httpx.Response(200, json={"token": "t"}), "unexpected answer"),
        (httpx.Response(200, content=b"x" * 20_000), "answered 200"),
    ],
)
async def test_redeem_pairing_code_errors(
    response: httpx.Response, message: str
) -> None:
    async with _client(httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(PairingError, match=message):
            await redeem_pairing_code(http, URL, "ABCD-EFGH")


async def test_redeem_pairing_code_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(httpx.MockTransport(handler)) as http:
        with pytest.raises(PairingError, match="Can't reach"):
            await redeem_pairing_code(http, URL, "ABCD-EFGH")


async def test_runtime_starts_restarts_and_stops_the_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []
    cancelled: list[str] = []

    async def fake_loop(name: str) -> None:
        started.append(name)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    monkeypatch.setattr(central_module, "run_sync_loop", lambda _: fake_loop("sync"))
    monkeypatch.setattr(central_module, "run_usage_loop", lambda _: fake_loop("usage"))
    conn = await aiosqlite.connect(":memory:")
    await apply_migrations(conn)
    runtime = CentralRuntime(conn)
    try:
        await runtime.start(CentralSettings(URL, "mcd_a"))
        await asyncio.sleep(0)
        assert runtime.running and runtime.url == URL
        assert state.usage_meter is not None

        await runtime.start(CentralSettings("https://other.example.com", "mcd_b"))
        await asyncio.sleep(0)
        assert runtime.url == "https://other.example.com"
        assert sorted(cancelled) == ["sync", "usage"]

        await runtime.stop()
        assert not runtime.running and runtime.url is None
        assert state.usage_meter is None
        assert len(started) == 4 and len(cancelled) == 4
    finally:
        await runtime.stop()
        await conn.close()


def _tunnel_body(**overrides: object) -> dict[str, object]:
    tunnel: dict[str, object] = {
        "tunnel_token": make_token(),
        "hostname": "cam.example.com",
        "team_domain": TEAM,
        "aud": AUD,
    }
    tunnel.update(overrides)
    return tunnel


async def _redeem_with(tunnel: object) -> PairedSite:
    body = {
        "token": "mcd_new",
        "site": {"name": "Casa", "hostname": "cam.example.com"},
        "tunnel": tunnel,
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    async with _client(transport) as http:
        return await redeem_pairing_code(http, URL, "ABCD-EFGH")


async def test_redeem_pairing_code_reads_the_tunnel() -> None:
    body = _tunnel_body()
    paired = await _redeem_with(body)

    assert paired.tunnel is not None and not paired.tunnel_invalid
    assert paired.tunnel.token == body["tunnel_token"]
    assert paired.tunnel.access.hostname == "cam.example.com"
    assert str(body["tunnel_token"]) not in repr(paired)

    without = await _redeem_with(None)
    assert without.tunnel is None and not without.tunnel_invalid


@pytest.mark.parametrize(
    "tunnel",
    [
        "not an object",
        _tunnel_body(tunnel_token="bm90IGEgdG9rZW4="),
        _tunnel_body(hostname="localhost"),
        _tunnel_body(team_domain="https://evil.example.com"),
        _tunnel_body(aud="short"),
        _tunnel_body(aud=None),
    ],
)
async def test_redeem_pairing_code_flags_an_invalid_tunnel(tunnel: object) -> None:
    paired = await _redeem_with(tunnel)
    assert paired.tunnel is None and paired.tunnel_invalid
    assert paired.device_token == "mcd_new"


async def test_redeem_pairing_code_tunnel_unavailable() -> None:
    response = httpx.Response(503, json={"error": "tunnel_unavailable"})
    async with _client(httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(PairingError, match="the code still works"):
            await redeem_pairing_code(http, URL, "ABCD-EFGH")
