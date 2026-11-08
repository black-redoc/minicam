"""Where the app-paired dashboard connection is stored (ADR 0009): central.json.

Kept apart from app/services/central.py (the sync runtime) so the settings
module can read it without importing the app state.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

CENTRAL_FILE = "central.json"
_DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent


def central_dir() -> Path:
    """Directory holding central.json (the checkout by default)."""
    return Path(os.environ.get("MINICAM_CENTRAL_DIR") or _DEFAULT_DIR)


def normalize_central_url(url: str) -> str | None:
    """`https://host[:port]` without path, or None. Plain http only to
    localhost, for development, like MINICAM_CENTRAL_URL."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    local = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
    if not (parts.scheme == "https" or local) or not parts.hostname:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return None
    host = parts.hostname + (f":{port}" if port else "")
    return f"{parts.scheme}://{host}"


@dataclass(frozen=True, slots=True)
class CentralSettings:
    url: str
    device_token: str = field(repr=False)


def read_central_file(directory: Path | None = None) -> CentralSettings | None:
    """central.json, or None when the app never paired this device (or the
    file is unreadable: then central mode is simply off)."""
    path = (directory or central_dir()) / CENTRAL_FILE
    try:
        data = json.loads(path.read_text())
        url = normalize_central_url(str(data["url"]))
        token = str(data["device_token"])
    except OSError, ValueError, TypeError, KeyError:
        return None
    if url is None or not token:
        return None
    return CentralSettings(url=url, device_token=token)


def write_central_file(
    settings: CentralSettings, directory: Path | None = None
) -> None:
    path = (directory or central_dir()) / CENTRAL_FILE
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": settings.url, "device_token": settings.device_token}, f)
    os.replace(tmp, path)


def remove_central_file(directory: Path | None = None) -> None:
    ((directory or central_dir()) / CENTRAL_FILE).unlink(missing_ok=True)
