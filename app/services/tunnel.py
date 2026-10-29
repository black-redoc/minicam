"""Cloudflare Tunnel set up from minicam-mobile (minicam ADR 0008).

The app creates the tunnel, DNS record and Access application with its own
Cloudflare API token and hands minicam only what the device needs: the
tunnel token, the public hostname, the Access team domain and the AUD tag.

minicam is unprivileged, so it never installs or runs cloudflared itself. It
writes a request file that a root helper (deploy/tunnel/minicam-tunnel,
started by minicam-tunnel.path) acts on, and reads the helper's status file:

    <dir>/tunnel.json            Access settings (hostname, team domain, AUD)
    <dir>/.tunnel/request.json   {"action": "install", "token": ...} or "remove"
    <dir>/.tunnel/status.json    written by the helper: {"state", "message"}

The helper validates the request again: it trusts nothing minicam writes.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent
SETTINGS_FILE = "tunnel.json"
HELPER_DIR = ".tunnel"
REQUEST_FILE = "request.json"
STATUS_FILE = "status.json"

_HOSTNAME_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_TEAM_DOMAIN = re.compile(
    r"https://[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com"
)
_AUD = re.compile(r"[0-9a-f]{64}")
_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
_MAX_TOKEN_LENGTH = 2048


def tunnel_dir() -> Path:
    """Directory holding tunnel.json and .tunnel/ (the checkout by default)."""
    return Path(os.environ.get("MINICAM_TUNNEL_DIR") or _DEFAULT_DIR)


def is_valid_hostname(hostname: str) -> bool:
    """A public DNS name with at least two labels, lowercase."""
    if len(hostname) > 253:
        return False
    labels = hostname.split(".")
    return len(labels) >= 2 and all(
        _HOSTNAME_LABEL.fullmatch(label) for label in labels
    )


def is_valid_team_domain(team_domain: str) -> bool:
    return _TEAM_DOMAIN.fullmatch(team_domain) is not None


def is_valid_aud(aud: str) -> bool:
    return _AUD.fullmatch(aud) is not None


def is_valid_tunnel_token(token: str) -> bool:
    """Cloudflare's tunnel token: base64 of {"a": account, "t": tunnel, "s": secret}."""
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        return False
    try:
        decoded = json.loads(base64.b64decode(token, validate=True))
    except binascii.Error, ValueError:
        return False
    if not isinstance(decoded, dict):
        return False
    account, tunnel, secret = decoded.get("a"), decoded.get("t"), decoded.get("s")
    if not (
        isinstance(account, str) and isinstance(tunnel, str) and isinstance(secret, str)
    ):
        return False
    try:
        uuid.UUID(tunnel)
    except ValueError:
        return False
    return _ACCOUNT_ID.fullmatch(account) is not None and len(secret) > 0


@dataclass(frozen=True, slots=True)
class AccessSettings:
    hostname: str
    team_domain: str
    aud: str


@dataclass(frozen=True, slots=True)
class TunnelSettings:
    """What tunnel.json holds.

    access is None once the tunnel has been removed; the file itself stays,
    so 127.0.0.1 is still treated as a trusted proxy (WAN) and a tunnel that
    is still shutting down can never reach minicam as LAN.
    """

    access: AccessSettings | None


def read_tunnel_settings(directory: Path | None = None) -> TunnelSettings | None:
    """tunnel.json, or None when the app never set up a tunnel.

    A file that can't be read or parsed still counts as "a tunnel was set up"
    with no Access settings: the safe reading is WAN-and-fail-closed.
    """
    path = (directory or tunnel_dir()) / SETTINGS_FILE
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return TunnelSettings(access=None)
    try:
        data = json.loads(raw)
        access = data.get("access")
        if access is None:
            return TunnelSettings(access=None)
        settings = AccessSettings(
            hostname=str(access["hostname"]),
            team_domain=str(access["team_domain"]),
            aud=str(access["aud"]),
        )
    except ValueError, TypeError, KeyError, AttributeError:
        return TunnelSettings(access=None)
    return TunnelSettings(access=settings)


class TunnelState(StrEnum):
    NONE = "none"
    INSTALLING = "installing"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TunnelStatus:
    available: bool
    state: TunnelState
    hostname: str | None
    message: str | None


def _write_private(path: Path, content: str) -> None:
    """Write content to path atomically, readable by this user only (0600)."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(tmp, path)


class TunnelService:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._helper_dir = directory / HELPER_DIR

    @property
    def available(self) -> bool:
        """The helper is installed (the image creates .tunnel/ for it)."""
        return self._helper_dir.is_dir()

    def status(self) -> TunnelStatus:
        settings = read_tunnel_settings(self._dir)
        hostname = settings.access.hostname if settings and settings.access else None
        if (self._helper_dir / REQUEST_FILE).exists():
            return TunnelStatus(self.available, TunnelState.INSTALLING, hostname, None)
        state, message = self._helper_status()
        return TunnelStatus(self.available, state, hostname, message)

    def _helper_status(self) -> tuple[TunnelState, str | None]:
        try:
            data = json.loads((self._helper_dir / STATUS_FILE).read_text())
            state = TunnelState(data["state"])
            message = data.get("message")
        except FileNotFoundError:
            return TunnelState.NONE, None
        except OSError, ValueError, TypeError, KeyError:
            return TunnelState.ERROR, "The tunnel helper's status could not be read"
        return state, message if isinstance(message, str) else None

    def request_setup(self, tunnel_token: str, access: AccessSettings) -> None:
        """Store the Access settings, then ask the helper to install the tunnel.

        Settings first: by the time cloudflared forwards a request, minicam
        already treats 127.0.0.1 as WAN and verifies Access tokens.
        """
        settings = {
            "access": {
                "hostname": access.hostname,
                "team_domain": access.team_domain,
                "aud": access.aud,
            }
        }
        _write_private(self._dir / SETTINGS_FILE, json.dumps(settings))
        request = {"action": "install", "token": tunnel_token}
        _write_private(self._helper_dir / REQUEST_FILE, json.dumps(request))

    def request_removal(self) -> None:
        """Ask the helper to stop the tunnel and drop the Access settings.

        tunnel.json stays (without Access settings) so loopback traffic is
        still WAN, which fails closed without Access settings.
        """
        _write_private(self._dir / SETTINGS_FILE, json.dumps({"access": None}))
        _write_private(
            self._helper_dir / REQUEST_FILE, json.dumps({"action": "remove"})
        )
