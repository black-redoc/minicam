"""Tests for app.services.update (ADR 0013)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.update import (
    CHECK_INTERVAL_SECONDS,
    MIN_REFRESH_SECONDS,
    STALE_STATUS_SECONDS,
    UpdateError,
    UpdateService,
    UpdateState,
    bundle_name,
    latest_from_release,
    parse_tag,
    read_version,
)


def release(tag: str = "v0.7.0", **overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": bundle_name(tag)},
            {"name": bundle_name(tag) + ".minisig"},
            {"name": f"minicam-{tag}-raspios-trixie-arm64.img.xz"},
        ],
    }
    data.update(overrides)
    return data


class Harness:
    def __init__(self, directory: Path, version: str = "0.6.0") -> None:
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "camia"\nversion = "{version}"\n'
        )
        self.helper = directory / ".update"
        self.helper.mkdir()
        self.release: dict[str, Any] | Exception = release()
        self.fetches = 0
        self.now = 1000.0
        self.wall = 1_800_000_000.0
        self.service = UpdateService(
            directory,
            fetch_release=self._fetch,
            clock=lambda: self.now,
            wall_clock=lambda: self.wall,
        )

    async def _fetch(self) -> dict[str, Any]:
        self.fetches += 1
        if isinstance(self.release, Exception):
            raise self.release
        return self.release

    def write_status(self, **data: object) -> None:
        (self.helper / "status.json").write_text(json.dumps(data))


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.mark.parametrize(
    ("tag", "parsed"),
    [
        ("v0.6.0", (0, 6, 0)),
        ("v10.0.12", (10, 0, 12)),
        ("0.6.0", None),
        ("v1.2", None),
        ("v01.2.3", None),
        ("v1.2.3-rc1", None),
        (None, None),
    ],
)
def test_parse_tag(tag: object, parsed: tuple[int, int, int] | None) -> None:
    assert parse_tag(tag) == parsed


def test_read_version(tmp_path: Path) -> None:
    Harness(tmp_path, "1.2.3")
    assert read_version(tmp_path) == "v1.2.3"


def test_a_release_needs_its_signed_bundle() -> None:
    assert latest_from_release(release()) == "v0.7.0"
    assert (
        latest_from_release(release(assets=[{"name": bundle_name("v0.7.0")}])) is None
    )
    assert latest_from_release(release(assets="nope")) is None


@pytest.mark.parametrize(
    "overrides",
    [{"draft": True}, {"prerelease": True}, {"tag_name": "nightly"}],
)
def test_drafts_prereleases_and_odd_tags_are_not_offered(
    overrides: dict[str, Any],
) -> None:
    assert latest_from_release(release(**overrides)) is None


async def test_offers_a_newer_release(harness: Harness) -> None:
    await harness.service.check()

    status = harness.service.status()
    assert status.available
    assert status.current == "v0.6.0"
    assert status.latest == "v0.7.0"
    assert status.update_available
    assert status.state is UpdateState.IDLE
    assert status.checked_at is not None


async def test_the_same_or_an_older_release_is_not_an_update(
    harness: Harness,
) -> None:
    harness.release = release("v0.6.0")
    await harness.service.check()

    assert not harness.service.status().update_available


async def test_checks_github_at_most_every_six_hours(harness: Harness) -> None:
    await harness.service.check()
    harness.now += CHECK_INTERVAL_SECONDS - 1
    await harness.service.check()
    assert harness.fetches == 1

    harness.now += 1
    await harness.service.check()
    assert harness.fetches == 2


async def test_a_refresh_checks_again_after_a_minute(harness: Harness) -> None:
    await harness.service.check()
    harness.now += MIN_REFRESH_SECONDS - 1
    await harness.service.check(refresh=True)
    assert harness.fetches == 1

    harness.now += 1
    await harness.service.check(refresh=True)
    assert harness.fetches == 2


async def test_a_failed_check_keeps_the_last_answer(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.service.check()
    harness.release = httpx.ConnectError("offline")
    harness.now += CHECK_INTERVAL_SECONDS

    await harness.service.check()

    assert harness.service.status().latest == "v0.7.0"
    assert "Could not check for minicam updates" in caplog.text


async def test_without_the_helper_nothing_is_offered(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.helper.rmdir()
    await harness.service.check()

    status = harness.service.status()
    assert not status.available
    assert not status.update_available
    with pytest.raises(UpdateError, match="can't update itself"):
        harness.service.request_install("v0.7.0")


async def test_request_install_writes_a_private_request(harness: Harness) -> None:
    await harness.service.check()

    harness.service.request_install("v0.7.0")

    request = harness.helper / "request.json"
    assert json.loads(request.read_text()) == {
        "action": "install",
        "version": "v0.7.0",
    }
    assert request.stat().st_mode & 0o777 == 0o600
    assert harness.service.status().state is UpdateState.REQUESTED


async def test_only_the_offered_release_can_be_installed(harness: Harness) -> None:
    with pytest.raises(UpdateError, match="not an available update"):
        harness.service.request_install("v0.7.0")  # not checked yet
    await harness.service.check()
    for version in ("v0.8.0", "v0.6.0", "v0.5.0"):
        with pytest.raises(UpdateError, match="not an available update"):
            harness.service.request_install(version)


async def test_one_update_at_a_time(harness: Harness) -> None:
    await harness.service.check()
    harness.write_status(state="downloading", version="v0.7.0", updated_at=harness.wall)

    with pytest.raises(UpdateError, match="already in progress"):
        harness.service.request_install("v0.7.0")


def test_reports_the_helper_status(harness: Harness) -> None:
    harness.write_status(
        state="installing",
        version="v0.7.0",
        message="Unpacking minicam v0.7.0",
        updated_at=harness.wall - 10,
    )

    status = harness.service.status()
    assert status.state is UpdateState.INSTALLING
    assert status.version == "v0.7.0"
    assert status.message == "Unpacking minicam v0.7.0"


def test_a_helper_that_stopped_reporting_is_an_error(harness: Harness) -> None:
    harness.write_status(
        state="downloading",
        version="v0.7.0",
        updated_at=harness.wall - STALE_STATUS_SECONDS - 1,
    )

    status = harness.service.status()
    assert status.state is UpdateState.ERROR
    assert status.message == "The update stopped before it finished"


def test_finished_states_never_go_stale(harness: Harness) -> None:
    harness.write_status(state="done", version="v0.7.0", updated_at=0)

    assert harness.service.status().state is UpdateState.DONE


@pytest.mark.parametrize("content", ["not json", json.dumps({"state": "weird"})])
def test_an_unreadable_status_is_an_error(harness: Harness, content: str) -> None:
    (harness.helper / "status.json").write_text(content)

    status = harness.service.status()
    assert status.state is UpdateState.ERROR
    assert status.message == "The update helper's status could not be read"
