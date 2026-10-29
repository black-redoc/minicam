"""Fixtures shared by every test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.services.recording import runtime as recording_runtime


@pytest.fixture(autouse=True)
def isolated_recordings_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point the recording service singleton at a throwaway directory.

    Tests that run main.app's real lifespan start the singleton, which deletes
    every clip its (temporary) database doesn't know: against the real
    app/data/recordings that wiped the operator's clips on every test run.
    """
    recordings_dir = tmp_path_factory.mktemp("recordings")
    monkeypatch.setattr(recording_runtime.service, "_dir", recordings_dir)
    yield recordings_dir


@pytest.fixture(autouse=True)
def isolated_tunnel_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep tunnel.json and .tunnel/ (app/services/tunnel.py) out of the checkout.

    A tunnel.json in the checkout would change every test's trusted proxies
    and Cloudflare Access settings.
    """
    tunnel_dir = tmp_path_factory.mktemp("tunnel")
    monkeypatch.setenv("MINICAM_TUNNEL_DIR", str(tunnel_dir))
    return tunnel_dir
