"""Fail when uv.lock contains a source-only package that is not allowlisted.

uv has no "never build from source except these packages" setting, so this
check enforces an sdist allowlist instead: a package with an sdist and no
wheel runs its build backend (arbitrary code) on every install, and needs an
explicit, reviewed entry in SDIST_ALLOWLIST below.

Used by the python job in .github/workflows/ci.yml.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

# Pure-Python packages that publish no wheel, pinned to the reviewed version.
SDIST_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("onvif-zeep", "0.2.12"),
        ("wsdiscovery", "2.1.2"),
    }
)


def source_only_packages(lock: dict[str, object]) -> list[tuple[str, str]]:
    """Return (name, version) of every locked package with an sdist but no wheel."""
    found = []
    packages = lock.get("package", [])
    assert isinstance(packages, list)
    for package in packages:
        if "sdist" in package and not package.get("wheels"):
            found.append((package["name"], package["version"]))
    return found


def violations(lock: dict[str, object]) -> list[tuple[str, str]]:
    """Return the source-only packages missing from the allowlist."""
    return [p for p in source_only_packages(lock) if p not in SDIST_ALLOWLIST]


def main(argv: list[str]) -> int:
    lock_path = Path(argv[1]) if len(argv) > 1 else Path("uv.lock")
    with lock_path.open("rb") as file:
        lock = tomllib.load(file)
    bad = violations(lock)
    for name, version in bad:
        print(
            f"{name}=={version} has no wheel and is not in the sdist allowlist "
            "(SDIST_ALLOWLIST in .github/scripts/check-sdist-allowlist.py)",
            file=sys.stderr,
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
