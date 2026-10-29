"""Tests for deploy/tunnel/minicam-tunnel, the root helper (ADR 0008).

The helper runs against a temporary root with fake commands, download and
unit state instead of apt, systemctl and the network.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import stat
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.test_tunnel_service import make_token

_HELPER = Path(__file__).resolve().parents[2] / "deploy" / "tunnel" / "minicam-tunnel"


@pytest.fixture(scope="module")
def helper_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("minicam_tunnel", str(_HELPER))
    spec = importlib.util.spec_from_loader("minicam_tunnel", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeSystem:
    def __init__(self, module: ModuleType, key: bytes, active: bool = True) -> None:
        self.error: type[Exception] = module.HelperError
        self.commands: list[list[str]] = []
        self.key = key
        self.active = active
        self.failing: str | None = None

    def run(self, argv: Sequence[str]) -> None:
        self.commands.append(list(argv))
        if self.failing is not None and argv[0] == self.failing:
            raise self.error(f"{argv[0]} failed: boom")

    def fetch(self, url: str) -> bytes:
        return self.key


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "opt" / "minicam" / ".tunnel").mkdir(parents=True)
    return tmp_path


def _helper(module: ModuleType, root: Path, system: FakeSystem) -> object:
    return module.Helper(
        root=root,
        runner=system.run,
        fetcher=system.fetch,
        is_active=lambda: system.active,
        sleep=lambda _: None,
    )


def _request(root: Path, request: object) -> None:
    path = root / "opt" / "minicam" / ".tunnel" / "request.json"
    path.write_text(request if isinstance(request, str) else json.dumps(request))


def _status(root: Path) -> dict[str, object]:
    path = root / "opt" / "minicam" / ".tunnel" / "status.json"
    return dict(json.loads(path.read_text()))


def _pinned_key(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = b"fake cloudflare key"
    monkeypatch.setattr(module, "KEY_SHA256", hashlib.sha256(key).hexdigest())
    return key


def test_install_sets_up_cloudflared_and_starts_the_unit(
    helper_module: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = FakeSystem(helper_module, _pinned_key(helper_module, monkeypatch))
    token = make_token()
    _request(root, {"action": "install", "token": token})

    _helper(helper_module, root, system).handle()

    assert _status(root) == {"state": "running", "message": None}
    assert not (root / "opt/minicam/.tunnel/request.json").exists()
    assert (root / "usr/share/keyrings/cloudflare-main.gpg").read_bytes() == system.key
    sources = (root / "etc/apt/sources.list.d/cloudflared.list").read_text()
    assert "signed-by=/usr/share/keyrings/cloudflare-main.gpg" in sources
    env = root / "etc/minicam/tunnel.env"
    assert env.read_text() == f"TUNNEL_TOKEN={token}\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert system.commands == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "--no-install-recommends", "cloudflared"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "minicam-cloudflared.service"],
        ["systemctl", "restart", "minicam-cloudflared.service"],
    ]
    assert all(token not in " ".join(argv) for argv in system.commands)


def test_install_rejects_a_key_that_does_not_match_the_pin(
    helper_module: ModuleType, root: Path
) -> None:
    system = FakeSystem(helper_module, b"attacker key")
    _request(root, {"action": "install", "token": make_token()})

    _helper(helper_module, root, system).handle()

    status = _status(root)
    assert status["state"] == "error"
    assert "does not match" in str(status["message"])
    assert system.commands == []
    assert not (root / "etc/minicam/tunnel.env").exists()


def test_install_reports_command_failures(
    helper_module: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = FakeSystem(helper_module, _pinned_key(helper_module, monkeypatch))
    system.failing = "apt-get"
    _request(root, {"action": "install", "token": make_token()})

    _helper(helper_module, root, system).handle()

    assert _status(root) == {"state": "error", "message": "apt-get failed: boom"}


def test_install_reports_a_tunnel_that_does_not_start(
    helper_module: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = FakeSystem(
        helper_module, _pinned_key(helper_module, monkeypatch), active=False
    )
    _request(root, {"action": "install", "token": make_token()})

    _helper(helper_module, root, system).handle()

    status = _status(root)
    assert status["state"] == "error"
    assert "did not start" in str(status["message"])


def test_remove_stops_the_unit_and_deletes_the_token(
    helper_module: ModuleType, root: Path
) -> None:
    system = FakeSystem(helper_module, b"")
    env = root / "etc/minicam/tunnel.env"
    env.parent.mkdir(parents=True)
    env.write_text("TUNNEL_TOKEN=x\n")
    _request(root, {"action": "remove"})

    _helper(helper_module, root, system).handle()

    assert system.commands == [
        ["systemctl", "disable", "--now", "minicam-cloudflared.service"]
    ]
    assert not env.exists()
    assert _status(root) == {"state": "none", "message": None}


@pytest.mark.parametrize(
    "request_body",
    [
        "not json",
        [],
        {"action": "install"},
        {"action": "install", "token": "nope"},
        {"action": "install", "token": 5},
        {"action": "remove", "extra": 1},
        {"action": "install", "token": make_token(), "extra": "x"},
        {"action": "reboot"},
    ],
)
def test_malformed_requests_are_rejected_and_consumed(
    helper_module: ModuleType, root: Path, request_body: object
) -> None:
    system = FakeSystem(helper_module, b"")
    _request(root, request_body)

    _helper(helper_module, root, system).handle()

    assert _status(root)["state"] == "error"
    assert system.commands == []
    assert not (root / "opt/minicam/.tunnel/request.json").exists()


def test_no_request_does_nothing(helper_module: ModuleType, root: Path) -> None:
    system = FakeSystem(helper_module, b"")
    _helper(helper_module, root, system).handle()
    assert system.commands == []
    assert not (root / "opt/minicam/.tunnel/status.json").exists()


def test_token_rules_match_minicam(helper_module: ModuleType) -> None:
    from app.services.tunnel import is_valid_tunnel_token

    for token in (make_token(), make_token(t="x"), make_token(a="A" * 32), "", "x"):
        assert helper_module.is_valid_token(token) is is_valid_tunnel_token(token)
