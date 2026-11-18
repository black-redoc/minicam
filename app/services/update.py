"""Updates to a newer minicam release from minicam-mobile (ADR 0013).

minicam only finds out whether a newer release exists and asks for it; a
root helper (deploy/update/minicam-update, started by minicam-update.path)
downloads, verifies the minisign signature, installs and restarts:

    <dir>/.update/request.json   {"action": "install", "version": "vX.Y.Z"}
    <dir>/.update/status.json    written by the helper: {"state", "version", "message"}

Releases come from one fixed GitHub repository; nothing the client sends
picks where code is downloaded from. A release is offered only once its
signed update bundle is attached (the image workflow uploads it).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent
HELPER_DIR = ".update"
REQUEST_FILE = "request.json"
STATUS_FILE = "status.json"
REPOSITORY = "black-redoc/minicam"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL_SECONDS = 6 * 3600.0
MIN_REFRESH_SECONDS = 60.0
# A helper that stopped reporting for this long died mid-update (power cut,
# killed): the state is shown as an error so the admin can try again.
STALE_STATUS_SECONDS = 30 * 60.0
_TAG = re.compile(r"v(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})")


def update_dir() -> Path:
    """Directory holding .update/ (the checkout by default)."""
    return Path(os.environ.get("MINICAM_UPDATE_DIR") or _DEFAULT_DIR)


def parse_tag(tag: object) -> tuple[int, int, int] | None:
    """(major, minor, patch) of a `vX.Y.Z` release tag, or None."""
    if not isinstance(tag, str):
        return None
    match = _TAG.fullmatch(tag)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def bundle_name(tag: str) -> str:
    return f"minicam-{tag}-linux-arm64.tar.gz"


def read_version(directory: Path) -> str:
    """This installation's version, `vX.Y.Z`, from its pyproject.toml."""
    with (directory / "pyproject.toml").open("rb") as f:
        version = tomllib.load(f)["project"]["version"]
    return f"v{version}"


class UpdateState(StrEnum):
    IDLE = "idle"
    REQUESTED = "requested"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    RESTARTING = "restarting"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    available: bool
    current: str
    latest: str | None
    update_available: bool
    state: UpdateState
    version: str | None
    message: str | None
    checked_at: str | None


class UpdateError(Exception):
    """A request that can't be accepted; str() is safe to show."""


ReleaseFetcher = Callable[[], Awaitable[dict[str, Any]]]


async def fetch_latest_release() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "minicam-update",
            },
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected release response")
    return data


def latest_from_release(release: dict[str, Any]) -> str | None:
    """The release's tag if it is a final release with its update bundle."""
    tag = release.get("tag_name")
    if parse_tag(tag) is None or release.get("draft") or release.get("prerelease"):
        return None
    assert isinstance(tag, str)
    assets = release.get("assets")
    names = (
        {asset.get("name") for asset in assets if isinstance(asset, dict)}
        if isinstance(assets, list)
        else set()
    )
    wanted = bundle_name(tag)
    if wanted not in names or wanted + ".minisig" not in names:
        return None
    return tag


class UpdateService:
    def __init__(
        self,
        directory: Path,
        fetch_release: ReleaseFetcher = fetch_latest_release,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._dir = directory
        self._helper_dir = directory / HELPER_DIR
        self._fetch_release = fetch_release
        self._clock = clock
        self._wall_clock = wall_clock
        self._latest: str | None = None
        self._checked_at: datetime | None = None
        self._checked_mono: float | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """The helper is installed (the image creates .update/ for it)."""
        return self._helper_dir.is_dir()

    def current(self) -> str:
        return read_version(self._dir)

    async def check(self, refresh: bool = False) -> None:
        """Ask GitHub for the latest release, unless asked recently."""
        async with self._lock:
            now = self._clock()
            last = self._checked_mono
            min_age = MIN_REFRESH_SECONDS if refresh else CHECK_INTERVAL_SECONDS
            if last is not None and now - last < min_age:
                return
            try:
                release = await self._fetch_release()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Could not check for minicam updates: %s", exc)
                return
            self._latest = latest_from_release(release)
            self._checked_mono = now
            self._checked_at = datetime.now(UTC)

    def _is_newer(self, tag: str | None) -> bool:
        latest = parse_tag(tag)
        current = parse_tag(self.current())
        return latest is not None and current is not None and latest > current

    def status(self) -> UpdateStatus:
        current = self.current()
        state, version, message = self._helper_status()
        return UpdateStatus(
            available=self.available,
            current=current,
            latest=self._latest,
            update_available=self.available and self._is_newer(self._latest),
            state=state,
            version=version,
            message=message,
            checked_at=self._checked_at.isoformat() if self._checked_at else None,
        )

    def _helper_status(self) -> tuple[UpdateState, str | None, str | None]:
        if (self._helper_dir / REQUEST_FILE).exists():
            return UpdateState.REQUESTED, None, None
        try:
            data = json.loads((self._helper_dir / STATUS_FILE).read_text())
            state = UpdateState(data["state"])
            version = data.get("version")
            message = data.get("message")
            updated_at = data.get("updated_at")
        except FileNotFoundError:
            return UpdateState.IDLE, None, None
        except OSError, ValueError, TypeError, KeyError:
            return (
                UpdateState.ERROR,
                None,
                "The update helper's status could not be read",
            )
        version = version if isinstance(version, str) else None
        if state in _BUSY and (
            not isinstance(updated_at, int | float)
            or self._wall_clock() - updated_at > STALE_STATUS_SECONDS
        ):
            return UpdateState.ERROR, version, "The update stopped before it finished"
        return state, version, message if isinstance(message, str) else None

    def request_install(self, version: str) -> None:
        """Ask the helper to install version, which must be the offered release."""
        if not self.available:
            raise UpdateError("This installation can't update itself")
        if version != self._latest or not self._is_newer(version):
            raise UpdateError("That version is not an available update")
        if self._helper_status()[0] in _BUSY:
            raise UpdateError("An update is already in progress")
        request = {"action": "install", "version": version}
        path = self._helper_dir / REQUEST_FILE
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(request))
        os.replace(tmp, path)


_BUSY = frozenset(
    {
        UpdateState.REQUESTED,
        UpdateState.DOWNLOADING,
        UpdateState.VERIFYING,
        UpdateState.INSTALLING,
        UpdateState.RESTARTING,
    }
)
