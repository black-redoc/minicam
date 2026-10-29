"""Tests for .github/scripts/check-sdist-allowlist.py (dependency policy)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / ".github" / "scripts" / "check-sdist-allowlist.py"


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_sdist_allowlist", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_lock(path: Path, body: str) -> Path:
    lock = path / "uv.lock"
    lock.write_text("version = 1\n" + body)
    return lock


def test_allowlisted_and_wheel_packages_pass(
    checker: ModuleType, tmp_path: Path
) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "onvif-zeep"
version = "0.2.12"
sdist = { url = "https://example.invalid/onvif.tar.gz" }

[[package]]
name = "httpx"
version = "0.28.1"
sdist = { url = "https://example.invalid/httpx.tar.gz" }
wheels = [{ url = "https://example.invalid/httpx-0.28.1-py3-none-any.whl" }]
""",
    )
    assert checker.main(["check", str(lock)]) == 0


def test_unlisted_source_only_package_fails(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "evil"
version = "1.0.0"
sdist = { url = "https://example.invalid/evil.tar.gz" }
""",
    )
    assert checker.main(["check", str(lock)]) == 1
    assert "evil==1.0.0" in capsys.readouterr().err


def test_allowlist_is_pinned_to_the_version(
    checker: ModuleType, tmp_path: Path
) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "wsdiscovery"
version = "9.9.9"
sdist = { url = "https://example.invalid/wsd.tar.gz" }
""",
    )
    assert checker.main(["check", str(lock)]) == 1


def test_repository_lock_passes(checker: ModuleType) -> None:
    assert checker.main(["check", str(_ROOT / "uv.lock")]) == 0
