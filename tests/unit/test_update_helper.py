"""Tests for deploy/update/minicam-update, the root helper (ADR 0013).

The helper runs against a temporary root with a real update bundle and fake
commands (minisign, systemctl, chown), downloads and health checks.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import tarfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

_HELPER = Path(__file__).resolve().parents[2] / "deploy" / "update" / "minicam-update"


@pytest.fixture(scope="module")
def helper_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("minicam_update", str(_HELPER))
    spec = importlib.util.spec_from_loader("minicam_update", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _bundle(version: str, extra: dict[str, bytes] | None = None) -> bytes:
    files = {
        "pyproject.toml": f'[project]\nversion = "{version}"\n'.encode(),
        "main.py": f"# minicam {version}\n".encode(),
        "app/__init__.py": b"",
        "app/data/coco.txt": b"person\n",
        ".venv/bin/uvicorn": b"#!/bin/sh\n",
        "deploy/minicam.service": f"[Service] # {version}\n".encode(),
        "deploy/update/minicam-update": f"# helper {version}\n".encode(),
        "deploy/update/minicam-release.pub": b"untrusted comment: key\nRWQ\n",
    }
    files.update(extra or {})
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith("uvicorn") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


class FakeSystem:
    def __init__(self, module: ModuleType, root: Path) -> None:
        self.module = module
        self.root = root
        self.app = root / "opt" / "minicam"
        self.commands: list[list[str]] = []
        self.downloads: list[str] = []
        self.bundle = _bundle("0.7.0")
        self.comment = "minicam v0.7.0 linux-arm64"
        self.fail: str | None = None
        self.healthy = True
        self.now = 0.0
        self.statuses: list[str] = []
        self._install_current()

    def _install_current(self) -> None:
        app = self.app
        (app / ".update").mkdir(parents=True)
        (app / ".tunnel").mkdir()
        (app / "app" / "data" / "recordings").mkdir(parents=True)
        (app / "app" / "data" / "recordings" / "clip.mp4").write_bytes(b"clip")
        (app / "app" / "data" / "models").mkdir()
        (app / "app" / "data" / "coco.txt").write_text("old\n")
        (app / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
        (app / "main.py").write_text("# minicam 0.6.0\n")
        (app / "old_module.py").write_text("gone in 0.7.0\n")
        (app / ".env").write_text("SECRET=1\n")
        (app / "config.toml").write_text("[cameras]\n")
        (app / "minicam.db").write_bytes(b"database v6")
        key = self.root / "usr" / "local" / "share" / "minicam" / "minicam-release.pub"
        key.parent.mkdir(parents=True)
        key.write_text("untrusted comment: key\nRWQ\n")

    def request(self, version: str = "v0.7.0") -> None:
        (self.app / ".update" / "request.json").write_text(
            json.dumps({"action": "install", "version": version})
        )

    def run(self, argv: Sequence[str]) -> str:
        self.commands.append(list(argv))
        if self.fail is not None and " ".join(argv).startswith(self.fail):
            raise self.module.HelperError(f"{argv[0]} failed: boom")
        if argv[0] == "minisign":
            return self.comment + "\n"
        main = self.app / "main.py"
        if argv[:2] == ["systemctl", "start"] and "0.7.0" in main.read_text():
            # The new code ran its migrations.
            (self.app / "minicam.db").write_bytes(b"database v7")
        return ""

    def download(self, url: str, path: Path) -> None:
        self.downloads.append(url)
        path.write_bytes(b"signature" if url.endswith(".minisig") else self.bundle)

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def helper(self) -> object:
        helper = self.module.Helper(
            root=self.root,
            runner=self.run,
            downloader=self.download,
            healthy=lambda: self.healthy,
            sleep=self.sleep,
            clock=lambda: self.now,
        )
        original = helper.status

        def status(state: str, message: str | None = None) -> None:
            self.statuses.append(state)
            original(state, message)

        helper.status = status
        return helper

    def status(self) -> dict[str, object]:
        return json.loads((self.app / ".update" / "status.json").read_text())


@pytest.fixture
def system(helper_module: ModuleType, tmp_path: Path) -> FakeSystem:
    return FakeSystem(helper_module, tmp_path)


def test_parse_request(helper_module: ModuleType) -> None:
    raw = json.dumps({"action": "install", "version": "v1.2.3"})
    assert helper_module.parse_request(raw) == "v1.2.3"
    for bad in (
        "nope",
        "[]",
        json.dumps({"action": "install"}),
        json.dumps({"action": "install", "version": "1.2.3"}),
        json.dumps({"action": "install", "version": "v1.2.3", "url": "https://x"}),
        json.dumps({"action": "remove", "version": "v1.2.3"}),
    ):
        with pytest.raises(helper_module.HelperError):
            helper_module.parse_request(bad)


def test_installs_a_newer_signed_release(system: FakeSystem) -> None:
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    app = system.app
    assert system.status()["state"] == "done"
    assert system.status()["version"] == "v0.7.0"
    assert system.statuses == [
        "downloading",
        "verifying",
        "installing",
        "restarting",
        "done",
    ]
    # Fixed URLs on the one repository.
    base = "https://github.com/black-redoc/minicam/releases/download/v0.7.0/"
    assert system.downloads == [
        base + "minicam-v0.7.0-linux-arm64.tar.gz",
        base + "minicam-v0.7.0-linux-arm64.tar.gz.minisig",
    ]
    # New code in, old code gone, state kept.
    assert (app / "main.py").read_text() == "# minicam 0.7.0\n"
    assert not (app / "old_module.py").exists()
    assert (app / ".env").read_text() == "SECRET=1\n"
    assert (app / "config.toml").read_text() == "[cameras]\n"
    assert (app / "minicam.db").read_bytes() == b"database v7"
    assert (app / "app" / "data" / "recordings" / "clip.mp4").read_bytes() == b"clip"
    assert (app / "app" / "data" / "models").is_dir()
    assert (app / "app" / "data" / "coco.txt").read_text() == "person\n"
    assert (app / ".tunnel").is_dir()
    assert not (app / ".update" / "request.json").exists()
    # The release's system files are installed.
    unit = system.root / "etc" / "systemd" / "system" / "minicam.service"
    assert unit.read_text() == "[Service] # 0.7.0\n"
    helper = system.root / "usr" / "local" / "sbin" / "minicam-update"
    assert helper.stat().st_mode & 0o777 == 0o755
    # Nothing left behind.
    assert not (system.root / "opt" / "minicam.next").exists()
    assert not (system.root / "opt" / "minicam.prev").exists()
    assert not (system.root / "var" / "tmp" / "minicam-update").exists()


def test_verifies_the_signature_with_the_pinned_key(system: FakeSystem) -> None:
    system.request()
    system.helper().handle()  # type: ignore[attr-defined]

    [minisign] = [c for c in system.commands if c[0] == "minisign"]
    key = system.root / "usr/local/share/minicam/minicam-release.pub"
    assert minisign[:5] == ["minisign", "-V", "-Q", "-p", str(key)]


def test_a_bad_signature_installs_nothing(system: FakeSystem) -> None:
    system.fail = "minisign"
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["state"] == "error"
    assert (system.app / "main.py").read_text() == "# minicam 0.6.0\n"
    assert ["systemctl", "stop", "minicam.service"] not in system.commands


def test_a_signature_for_another_version_is_refused(system: FakeSystem) -> None:
    system.comment = "minicam v0.6.1 linux-arm64"
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status() == {
        "state": "error",
        "version": "v0.7.0",
        "message": "The update's signature is for another version",
        "updated_at": 0.0,
    }


def test_a_missing_release_key_installs_nothing(system: FakeSystem) -> None:
    (system.root / "usr/local/share/minicam/minicam-release.pub").unlink()
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["message"] == "This device has no release key"


@pytest.mark.parametrize("version", ["v0.6.0", "v0.5.9"])
def test_never_downgrades(system: FakeSystem, version: str) -> None:
    system.request(version)

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["state"] == "error"
    assert "is not newer than the installed v0.6.0" in str(system.status()["message"])
    assert system.downloads == []


def test_a_bundle_of_another_version_is_refused(system: FakeSystem) -> None:
    system.bundle = _bundle("0.6.5")
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["message"] == "The update contains another version"
    assert (system.app / "main.py").read_text() == "# minicam 0.6.0\n"


def test_a_bundle_never_brings_device_state(system: FakeSystem) -> None:
    system.bundle = _bundle(
        "0.7.0", {".env": b"SECRET=attacker\n", "minicam.db": b"other"}
    )
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["state"] == "done"
    assert (system.app / ".env").read_text() == "SECRET=1\n"
    assert (system.app / "minicam.db").read_bytes() == b"database v7"


def test_an_unhealthy_release_is_rolled_back(system: FakeSystem) -> None:
    system.healthy = False
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    app = system.app
    status = system.status()
    assert status["state"] == "error"
    assert status["message"] == ("minicam did not start; minicam v0.6.0 was restored")
    assert (app / "main.py").read_text() == "# minicam 0.6.0\n"
    assert (app / "old_module.py").exists()
    assert (app / "minicam.db").read_bytes() == b"database v6"
    assert (app / "app" / "data" / "recordings" / "clip.mp4").read_bytes() == b"clip"
    assert (app / "app" / "data" / "coco.txt").read_text() == "old\n"
    assert (app / ".env").read_text() == "SECRET=1\n"
    assert system.commands[-1] == ["systemctl", "start", "minicam.service"]


def test_a_failed_stop_restarts_the_old_minicam(system: FakeSystem) -> None:
    system.fail = "systemctl stop"
    system.request()

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["state"] == "error"
    assert (system.app / "main.py").read_text() == "# minicam 0.6.0\n"
    assert (system.app / "minicam.db").read_bytes() == b"database v6"


def test_a_failed_download_is_reported(
    system: FakeSystem, helper_module: ModuleType
) -> None:
    def broken(url: str, path: Path) -> None:
        raise helper_module.HelperError(f"Could not download {url}: offline")

    system.request()
    helper = system.helper()
    helper.download = broken  # type: ignore[attr-defined]

    helper.handle()  # type: ignore[attr-defined]

    assert str(system.status()["message"]).startswith("Could not download")


def test_a_bad_request_is_consumed_and_reported(system: FakeSystem) -> None:
    (system.app / ".update" / "request.json").write_text("{}")

    system.helper().handle()  # type: ignore[attr-defined]

    assert system.status()["message"] == "Unknown request"
    assert not (system.app / ".update" / "request.json").exists()


def test_no_request_does_nothing(system: FakeSystem) -> None:
    system.helper().handle()  # type: ignore[attr-defined]

    assert not (system.app / ".update" / "status.json").exists()
