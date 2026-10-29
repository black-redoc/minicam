"""Unit tests for app/services/tunnel.py and its effect on security settings."""

from __future__ import annotations

import base64
import json
import stat
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.dependencies import get_cloudflare_access_verifier
from app.security_settings import get_security_settings
from app.services.tunnel import (
    AccessSettings,
    TunnelService,
    TunnelState,
    is_valid_aud,
    is_valid_hostname,
    is_valid_team_domain,
    is_valid_tunnel_token,
    read_tunnel_settings,
)

AUD = "a" * 64
TEAM = "https://myteam.cloudflareaccess.com"


def make_token(**overrides: object) -> str:
    payload: dict[str, object] = {
        "a": "0123456789abcdef0123456789abcdef",
        "t": str(uuid.uuid4()),
        "s": base64.b64encode(b"secret-bytes").decode(),
    }
    payload.update(overrides)
    return base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MINICAM_JWT_SECRET", "unit-test-secret")
    for name in (
        "MINICAM_TRUSTED_PROXIES",
        "MINICAM_CF_ACCESS_TEAM_DOMAIN",
        "MINICAM_CF_ACCESS_AUD",
    ):
        monkeypatch.delenv(name, raising=False)
    get_security_settings.cache_clear()
    get_cloudflare_access_verifier.cache_clear()
    yield
    get_security_settings.cache_clear()
    get_cloudflare_access_verifier.cache_clear()


@pytest.fixture
def service(isolated_tunnel_dir: Path) -> TunnelService:
    (isolated_tunnel_dir / ".tunnel").mkdir()
    return TunnelService(isolated_tunnel_dir)


def _access() -> AccessSettings:
    return AccessSettings(hostname="cam.example.com", team_domain=TEAM, aud=AUD)


@pytest.mark.parametrize(
    ("token", "valid"),
    [
        (make_token(), True),
        ("", False),
        ("not base64!", False),
        (base64.b64encode(b"[1, 2]").decode(), False),
        (base64.b64encode(b"not json").decode(), False),
        (make_token(a="short"), False),
        (make_token(t="not-a-uuid"), False),
        (make_token(s=""), False),
        (make_token(s=5), False),
        ("A" * 4096, False),
    ],
)
def test_tunnel_token_validation(token: str, valid: bool) -> None:
    assert is_valid_tunnel_token(token) is valid


@pytest.mark.parametrize(
    ("hostname", "valid"),
    [
        ("cam.example.com", True),
        ("a-b.c.example.co", True),
        ("example", False),
        ("Cam.example.com", False),
        ("-cam.example.com", False),
        ("cam..example.com", False),
        ("cam.example.com/x", False),
        ("a" * 64 + ".example.com", False),
        (("a" * 60 + ".") * 5 + "com", False),
    ],
)
def test_hostname_validation(hostname: str, valid: bool) -> None:
    assert is_valid_hostname(hostname) is valid


def test_team_domain_and_aud_validation() -> None:
    assert is_valid_team_domain(TEAM)
    assert not is_valid_team_domain("http://myteam.cloudflareaccess.com")
    assert not is_valid_team_domain("https://evil.com/.cloudflareaccess.com")
    assert not is_valid_team_domain("https://myteam.cloudflareaccess.com/")
    assert is_valid_aud(AUD)
    assert not is_valid_aud("A" * 64)
    assert not is_valid_aud("a" * 63)


def test_unavailable_without_helper_dir(isolated_tunnel_dir: Path) -> None:
    status = TunnelService(isolated_tunnel_dir).status()
    assert not status.available
    assert status.state is TunnelState.NONE


def test_request_setup_writes_private_files(
    service: TunnelService, isolated_tunnel_dir: Path
) -> None:
    token = make_token()
    service.request_setup(token, _access())

    settings_file = isolated_tunnel_dir / "tunnel.json"
    request_file = isolated_tunnel_dir / ".tunnel" / "request.json"
    for path in (settings_file, request_file):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(request_file.read_text()) == {"action": "install", "token": token}
    assert token not in settings_file.read_text()
    assert read_tunnel_settings(isolated_tunnel_dir) is not None

    status = service.status()
    assert status.state is TunnelState.INSTALLING
    assert status.hostname == "cam.example.com"


@pytest.mark.parametrize(
    ("helper_status", "state", "message"),
    [
        ({"state": "running"}, TunnelState.RUNNING, None),
        ({"state": "error", "message": "apt failed"}, TunnelState.ERROR, "apt failed"),
        ({"state": "running", "message": 5}, TunnelState.RUNNING, None),
        ({"state": "bogus"}, TunnelState.ERROR, None),
    ],
)
def test_status_reads_helper_status(
    service: TunnelService,
    isolated_tunnel_dir: Path,
    helper_status: dict[str, object],
    state: TunnelState,
    message: str | None,
) -> None:
    status_file = isolated_tunnel_dir / ".tunnel" / "status.json"
    status_file.write_text(json.dumps(helper_status))
    status = service.status()
    assert status.state is state
    if state is not TunnelState.ERROR or helper_status["state"] == "error":
        assert status.message == message


def test_request_removal_keeps_loopback_trusted(
    service: TunnelService, isolated_tunnel_dir: Path
) -> None:
    service.request_setup(make_token(), _access())
    service.request_removal()

    request = json.loads((isolated_tunnel_dir / ".tunnel" / "request.json").read_text())
    assert request == {"action": "remove"}
    settings = read_tunnel_settings(isolated_tunnel_dir)
    assert settings is not None and settings.access is None

    security = get_security_settings()
    assert "127.0.0.1" in security.trusted_proxies
    assert security.cf_access_team_domain is None
    assert get_cloudflare_access_verifier() is None


def test_access_settings_from_tunnel(service: TunnelService) -> None:
    service.request_setup(make_token(), _access())

    security = get_security_settings()
    assert security.trusted_proxies == frozenset({"127.0.0.1"})
    assert security.cf_access_team_domain == TEAM
    assert security.cf_access_aud == AUD
    assert get_cloudflare_access_verifier() is not None


def test_env_access_settings_win(
    service: TunnelService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINICAM_TRUSTED_PROXIES", "10.0.0.2")
    monkeypatch.setenv(
        "MINICAM_CF_ACCESS_TEAM_DOMAIN", "https://env.cloudflareaccess.com"
    )
    monkeypatch.setenv("MINICAM_CF_ACCESS_AUD", "b" * 64)
    service.request_setup(make_token(), _access())

    security = get_security_settings()
    assert security.cf_access_team_domain == "https://env.cloudflareaccess.com"
    assert security.cf_access_aud == "b" * 64
    assert security.trusted_proxies == frozenset({"10.0.0.2", "127.0.0.1"})


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"access": {"hostname": "x"}}', '{"access": 5}']
)
def test_unreadable_settings_fail_closed(
    isolated_tunnel_dir: Path, content: str
) -> None:
    (isolated_tunnel_dir / "tunnel.json").write_text(content)

    settings = read_tunnel_settings(isolated_tunnel_dir)
    assert settings is not None and settings.access is None
    security = get_security_settings()
    assert "127.0.0.1" in security.trusted_proxies
    assert security.cf_access_aud is None


def test_no_tunnel_leaves_settings_unchanged() -> None:
    security = get_security_settings()
    assert security.trusted_proxies == frozenset()
    assert security.cf_access_team_domain is None


def test_settings_that_cannot_be_read_fail_closed(isolated_tunnel_dir: Path) -> None:
    (isolated_tunnel_dir / "tunnel.json").mkdir()

    settings = read_tunnel_settings(isolated_tunnel_dir)
    assert settings is not None and settings.access is None
    assert "127.0.0.1" in get_security_settings().trusted_proxies
