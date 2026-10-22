"""Tests for RecordingService: wiring between detection, clips and storage."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest

from app.models.config import AppConfig, RecordingConfig
from app.models.domain import Recording
from app.repositories.recording_repository import SQLiteRecordingRepository
from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot
from app.services.recording import runtime as recording_runtime
from app.services.recording.packets import ClipStart, EncodedPacket
from app.services.recording.runtime import RecordingService
from app.services.recording.storage import GIB, RESERVE_BYTES
from app.services.recording.writer import FinishedClip

_STARTED_AT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class FakeWriter:
    def __init__(self, path: Path, on_finished: Callable[[FinishedClip], None]) -> None:
        self.path = path
        self.on_finished = on_finished
        self.finished = False
        self.alive = True

    def write(self, packet: EncodedPacket) -> None: ...

    def finish(self) -> None:
        self.finished = True

    def join(self, timeout: float | None = None) -> bool:
        if timeout:
            self.alive = False
        return not self.alive


class Harness:
    def __init__(self, conn: aiosqlite.Connection, directory: Path) -> None:
        self.config = RecordingConfig(enabled=True, max_storage_gb=1)
        self.free = 100 * GIB
        self.now = 100.0
        self.writers: list[FakeWriter] = []
        self.open_error: Exception | None = None
        self.service = RecordingService(
            directory,
            connection=lambda: conn,
            config=lambda: self.config,
            open_writer=self._open_writer,
            get_free_bytes=lambda _dir: self.free,
            clock=lambda: self.now,
        )

    def _open_writer(
        self,
        start: ClipStart,
        path: Path,
        on_finished: Callable[[FinishedClip], None],
    ) -> FakeWriter:
        if self.open_error is not None:
            raise self.open_error
        writer = FakeWriter(path, on_finished)
        self.writers.append(writer)
        return writer


@pytest.fixture
def harness(db_connection: aiosqlite.Connection, tmp_path: Path) -> Harness:
    return Harness(db_connection, tmp_path / "recordings")


def _snapshot(*classes: str) -> DetectionSnapshot:
    return DetectionSnapshot(
        detections=[Detection(c, 0.9, 0, 0, 1, 1) for c in classes], sequence=0
    )


def _start(camera: str = "entrance") -> ClipStart:
    return ClipStart(camera, _STARTED_AT, lambda p: None)


def _clip(directory: Path, name: str = "a.mp4", size: int = 100) -> FinishedClip:
    path = directory / name
    path.write_bytes(b"x" * size)
    return FinishedClip("entrance", path, _STARTED_AT, 12.0, size)


async def test_start_removes_unfinished_and_unregistered_clips(
    harness: Harness, db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    directory = tmp_path / "recordings"
    directory.mkdir()
    repository = SQLiteRecordingRepository(db_connection)
    await repository.add("entrance", "kept.mp4", "t", 1.0, 1, ())
    await repository.add("entrance", "lost.mp4", "t", 1.0, 1, ())
    (directory / "kept.mp4").write_bytes(b"x")
    (directory / "orphan.mp4").write_bytes(b"x")
    (directory / "crashed.mp4.part").write_bytes(b"x")
    (directory / "notes.txt").write_bytes(b"x")
    (directory / "subdir").mkdir()

    await harness.service.start()

    assert sorted(p.name for p in directory.iterdir()) == [
        "kept.mp4",
        "notes.txt",
        "subdir",
    ]
    assert await repository.list_file_names() == {"kept.mp4"}


async def test_on_snapshot_arms_and_records_after_confirmation(
    harness: Harness,
) -> None:
    recorder = harness.service.recorder_for("entrance")
    recorder.arm = MagicMock()  # type: ignore[method-assign]
    recorder.record_until = MagicMock()  # type: ignore[method-assign]

    harness.service.on_snapshot("entrance", _snapshot("person"))
    recorder.record_until.assert_not_called()
    harness.service.on_snapshot("entrance", _snapshot("person"))

    recorder.arm.assert_called_with(True)
    recorder.record_until.assert_called_once_with(105.0)


async def test_on_snapshot_disarms_when_recording_is_off(harness: Harness) -> None:
    harness.config = RecordingConfig(enabled=False)
    recorder = harness.service.recorder_for("entrance")
    recorder.arm = MagicMock()  # type: ignore[method-assign]
    recorder.record_until = MagicMock()  # type: ignore[method-assign]

    for _ in range(3):
        harness.service.on_snapshot("entrance", _snapshot("person"))

    recorder.arm.assert_called_with(False)
    recorder.record_until.assert_not_called()


async def test_on_snapshot_ignores_unknown_and_forgotten_cameras(
    harness: Harness,
) -> None:
    harness.service.on_snapshot("ghost", _snapshot("person"))
    harness.service.recorder_for("entrance")
    harness.service.forget("entrance")
    harness.service.on_snapshot("entrance", _snapshot("person"))


async def test_open_clip_requires_a_started_service(harness: Harness) -> None:
    assert harness.service._open_clip(_start()) is None
    assert harness.writers == []


async def test_open_clip_names_the_file_after_the_start_time(
    harness: Harness, tmp_path: Path
) -> None:
    await harness.service.start()
    writer = harness.service._open_clip(_start())

    assert writer is harness.writers[0]
    assert harness.writers[0].path.parent == tmp_path / "recordings"
    assert harness.writers[0].path.name.startswith("20261022T120000Z-")
    assert harness.writers[0].path.suffix == ".mp4"


async def test_open_clip_refuses_when_disk_is_nearly_full(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.service.start()
    harness.free = RESERVE_BYTES - 1

    assert harness.service._open_clip(_start()) is None
    assert harness.service._open_clip(_start()) is None
    assert caplog.text.count("less than 1024 MiB free") == 1

    harness.now += 61
    harness.service._open_clip(_start())
    assert caplog.text.count("less than 1024 MiB free") == 2


async def test_open_clip_logs_and_refuses_on_open_errors(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.service.start()
    harness.open_error = OSError("read-only file system")

    assert harness.service._open_clip(_start()) is None
    assert "Could not start recording for camera 'entrance'" in caplog.text


async def test_finished_clip_is_registered_with_its_classes(
    harness: Harness, db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    await harness.service.start()
    harness.service.recorder_for("entrance")
    harness.service.on_snapshot("entrance", _snapshot("person"))
    harness.service.on_snapshot("entrance", _snapshot("person", "dog"))

    harness.service._on_clip_finished(_clip(tmp_path / "recordings"))
    await harness.service.shutdown()

    [recording] = await SQLiteRecordingRepository(db_connection).list_for_cameras(
        ["entrance"], None, 10
    )
    assert recording.file_name == "a.mp4"
    assert recording.started_at == "2026-10-22T12:00:00+00:00"
    assert recording.duration_seconds == 12.0
    assert recording.size_bytes == 100
    assert recording.classes == ("dog", "person")


async def test_finished_clip_applies_retention(
    harness: Harness, db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    directory = tmp_path / "recordings"
    await harness.service.start()
    repository = SQLiteRecordingRepository(db_connection)
    (directory / "old.mp4").write_bytes(b"x")
    await repository.add("entrance", "old.mp4", "t", 1.0, GIB, ())

    harness.service._on_clip_finished(_clip(directory, "new.mp4"))
    await harness.service.shutdown()

    assert await repository.list_file_names() == {"new.mp4"}
    assert not (directory / "old.mp4").exists()


async def test_register_failure_is_logged(
    harness: Harness, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.service.start()
    clip = _clip(tmp_path / "recordings")
    await harness.service._register(clip)
    await harness.service._register(clip)  # duplicate file name
    assert "Could not register recording a.mp4" in caplog.text


async def test_finished_clip_without_a_loop_is_left_for_startup_cleanup(
    harness: Harness, tmp_path: Path
) -> None:
    (tmp_path / "recordings").mkdir()
    harness.service._on_clip_finished(_clip(tmp_path / "recordings"))
    assert harness.service._pending == set()


async def test_finished_clip_after_the_loop_closed_is_ignored(
    harness: Harness, tmp_path: Path
) -> None:
    (tmp_path / "recordings").mkdir()
    loop = asyncio.new_event_loop()
    loop.close()
    harness.service._loop = loop
    harness.service._on_clip_finished(_clip(tmp_path / "recordings"))
    assert harness.service._pending == set()


async def test_shutdown_waits_for_writers(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    await harness.service.start()
    harness.service._open_clip(_start())
    stuck = MagicMock()
    stuck.join.return_value = False
    harness.service._writers.append(stuck)

    await harness.service.shutdown()

    assert harness.writers[0].alive is False
    assert "did not finish writing in time" in caplog.text


async def test_finished_writers_are_pruned(harness: Harness) -> None:
    await harness.service.start()
    harness.service._open_clip(_start())
    harness.writers[0].alive = False
    harness.service._open_clip(_start())
    assert harness.service._writers == [harness.writers[1]]


def _recording(file_name: str) -> Recording:
    return Recording(1, "entrance", file_name, "t", 1.0, 1, (), "t")


def test_path_for_accepts_generated_names(harness: Harness, tmp_path: Path) -> None:
    path = harness.service.path_for(_recording("20261022T120000Z-ab12cd34.mp4"))
    assert path == tmp_path / "recordings" / "20261022T120000Z-ab12cd34.mp4"
    assert harness.service.recordings_dir == tmp_path / "recordings"


@pytest.mark.parametrize("name", ["../etc/passwd.mp4", "a/b.mp4", "clip.txt"])
def test_path_for_rejects_anything_else(harness: Harness, name: str) -> None:
    with pytest.raises(ValueError):
        harness.service.path_for(_recording(name))


def test_free_bytes_uses_the_recordings_dir(harness: Harness) -> None:
    assert harness.service.free_bytes() == 100 * GIB


def test_singleton_reads_settings_from_config_toml() -> None:
    config = AppConfig(recordings=RecordingConfig(enabled=True, max_storage_gb=10))
    with patch("app.services.recording.runtime.get_config", return_value=config):
        assert recording_runtime.service._config() == config.recordings
