"""The test suite must never start the recording service on the real
app/data/recordings: its startup cleanup deletes every clip the (temporary)
test database doesn't know, i.e. all of the operator's clips."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.services.recording import runtime as recording_runtime


def test_real_lifespan_uses_a_throwaway_recordings_dir(
    e2e_client: TestClient, isolated_recordings_dir: Path
) -> None:
    assert recording_runtime.service.recordings_dir == isolated_recordings_dir
    assert recording_runtime.service.recordings_dir != recording_runtime.RECORDINGS_DIR
