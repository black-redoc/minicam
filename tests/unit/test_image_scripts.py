"""Tests for the Raspberry Pi OS image's shell scripts.

minicam-firstboot.sh runs against a temporary root (MINICAM_ROOT) instead of
the real /, and check-version.sh against this repository's pyproject.toml.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from app.security_settings import get_security_settings

_ROOT = Path(__file__).resolve().parents[2]
_FIRSTBOOT = _ROOT / "image" / "files" / "minicam-firstboot.sh"
_CHECK_VERSION = _ROOT / ".github" / "scripts" / "check-version.sh"

# The script chowns .env to the `minicam` user when it runs as root.
pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="runs the scripts as an unprivileged user"
)


def _version() -> str:
    with (_ROOT / "pyproject.toml").open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def _check_version(tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(_CHECK_VERSION), tag, str(_ROOT / "pyproject.toml")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_version_accepts_matching_tag() -> None:
    result = _check_version(f"v{_version()}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tag", ["v0.0.1", "0.2.0", "latest", "v1.2"])
def test_check_version_rejects_other_tags(tag: str) -> None:
    result = _check_version(tag)
    assert result.returncode == 1
    assert tag in result.stderr


def _device_root(tmp_path: Path, hostname: str = "raspberrypi") -> Path:
    root = tmp_path / "root"
    app = root / "opt" / "minicam"
    app.mkdir(parents=True)
    shutil.copy(_ROOT / ".env.example", app / ".env.example")
    etc = root / "etc"
    etc.mkdir()
    (etc / "hostname").write_text(f"{hostname}\n")
    (etc / "hosts").write_text(
        f"127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t{hostname}\n"
    )
    for iface, mac in (("lo", "00:00:00:00:00:00"), ("eth0", "dc:a6:32:12:ab:cd")):
        net = root / "sys" / "class" / "net" / iface
        net.mkdir(parents=True)
        (net / "address").write_text(f"{mac}\n")
    return root


def _firstboot(root: Path) -> None:
    subprocess.run(
        ["sh", str(_FIRSTBOOT)],
        env={**os.environ, "MINICAM_ROOT": str(root)},
        check=True,
    )


def _env(root: Path) -> dict[str, str]:
    lines = (root / "opt" / "minicam" / ".env").read_text().splitlines()
    pairs = (line.split("=", 1) for line in lines if re.match(r"^[A-Z_]+=", line))
    return dict(pairs)


def test_firstboot_creates_private_env_with_device_secret(tmp_path: Path) -> None:
    root = _device_root(tmp_path)
    _firstboot(root)

    env_file = root / "opt" / "minicam" / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    env = _env(root)
    assert re.fullmatch(r"[0-9a-f]{64}", env["MINICAM_JWT_SECRET"])
    assert env["MINICAM_TRUSTED_PROXIES"] == "127.0.0.1"
    assert env["MINICAM_SETUP_CLAIM"] == "led"
    assert env["MINICAM_WIFI_PROVISIONING"] == "nm"
    assert not (root / "opt" / "minicam" / ".env.tmp").exists()


def test_firstboot_env_is_accepted_by_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _device_root(tmp_path)
    _firstboot(root)

    for name, value in _env(root).items():
        monkeypatch.setenv(name, value)
    settings = get_security_settings()
    assert settings.jwt_secret == _env(root)["MINICAM_JWT_SECRET"]


def test_firstboot_secret_is_unique_per_device(tmp_path: Path) -> None:
    first = _device_root(tmp_path / "a")
    second = _device_root(tmp_path / "b")
    _firstboot(first)
    _firstboot(second)
    assert _env(first)["MINICAM_JWT_SECRET"] != _env(second)["MINICAM_JWT_SECRET"]


def test_firstboot_keeps_existing_env(tmp_path: Path) -> None:
    root = _device_root(tmp_path)
    env_file = root / "opt" / "minicam" / ".env"
    env_file.write_text("MINICAM_JWT_SECRET=mine\n")
    _firstboot(root)
    assert env_file.read_text() == "MINICAM_JWT_SECRET=mine\n"


def test_firstboot_renames_default_hostname_from_mac(tmp_path: Path) -> None:
    root = _device_root(tmp_path)
    _firstboot(root)
    assert (root / "etc" / "hostname").read_text() == "minicam-abcd\n"
    hosts = (root / "etc" / "hosts").read_text()
    assert "127.0.1.1\tminicam-abcd\n" in hosts
    assert "127.0.0.1\tlocalhost\n" in hosts


def test_firstboot_keeps_hostname_set_in_imager(tmp_path: Path) -> None:
    root = _device_root(tmp_path, hostname="garage-cam")
    _firstboot(root)
    assert (root / "etc" / "hostname").read_text() == "garage-cam\n"
    assert "127.0.1.1\tgarage-cam\n" in (root / "etc" / "hosts").read_text()
