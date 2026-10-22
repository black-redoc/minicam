"""Tests for the recording disk budget: selectable limits and retention."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from app.repositories.recording_repository import SQLiteRecordingRepository
from app.services.recording.storage import (
    GIB,
    RESERVE_BYTES,
    enforce_retention,
    free_bytes,
    is_option_available,
    storage_options,
)


def test_options_fit_free_space_minus_the_reserve() -> None:
    options = storage_options(free=6 * GIB + RESERVE_BYTES, used_by_recordings=0)
    assert [(o.gb, o.available) for o in options] == [
        (1, True),
        (5, True),
        (10, False),
        (50, False),
    ]


def test_space_used_by_recordings_counts_as_available() -> None:
    options = storage_options(free=RESERVE_BYTES + 2 * GIB, used_by_recordings=8 * GIB)
    assert [o.gb for o in options if o.available] == [1, 5, 10]


def test_nothing_fits_on_a_nearly_full_disk() -> None:
    assert not any(o.available for o in storage_options(free=GIB, used_by_recordings=0))


def test_is_option_available() -> None:
    assert is_option_available(5, free=10 * GIB, used_by_recordings=0)
    assert not is_option_available(50, free=10 * GIB, used_by_recordings=0)
    assert not is_option_available(3, free=10 * GIB, used_by_recordings=0)


def test_free_bytes_creates_the_directory(tmp_path: Path) -> None:
    directory = tmp_path / "recordings"
    assert free_bytes(directory) > 0
    assert directory.is_dir()


async def _add(
    repository: SQLiteRecordingRepository, directory: Path, name: str, size: int
) -> None:
    (directory / name).write_bytes(b"x")
    await repository.add("entrance", name, "2026-10-22T12:00:00+00:00", 1.0, size, ())


async def test_retention_deletes_oldest_clips_over_the_limit(
    db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    repository = SQLiteRecordingRepository(db_connection)
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        await _add(repository, tmp_path, name, GIB)

    deleted = await enforce_retention(
        repository, tmp_path, limit_bytes=2 * GIB, get_free_bytes=lambda: 100 * GIB
    )

    assert deleted == 1
    assert await repository.list_file_names() == {"b.mp4", "c.mp4"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.mp4", "c.mp4"]


async def test_retention_frees_the_reserve_on_a_full_disk(
    db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    repository = SQLiteRecordingRepository(db_connection)
    for name in ("a.mp4", "b.mp4"):
        await _add(repository, tmp_path, name, 100)
    free = iter([0, RESERVE_BYTES])

    deleted = await enforce_retention(
        repository, tmp_path, limit_bytes=GIB, get_free_bytes=lambda: next(free)
    )

    assert deleted == 1
    assert await repository.list_file_names() == {"b.mp4"}


async def test_retention_stops_when_nothing_is_left(
    db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    repository = SQLiteRecordingRepository(db_connection)
    await _add(repository, tmp_path, "a.mp4", 100)

    deleted = await enforce_retention(
        repository, tmp_path, limit_bytes=GIB, get_free_bytes=lambda: 0
    )

    assert deleted == 1
    assert await repository.total_size_bytes() == 0


async def test_retention_tolerates_an_already_missing_file(
    db_connection: aiosqlite.Connection, tmp_path: Path
) -> None:
    repository = SQLiteRecordingRepository(db_connection)
    await repository.add("entrance", "gone.mp4", "t", 1.0, 2 * GIB, ())

    deleted = await enforce_retention(
        repository, tmp_path, limit_bytes=GIB, get_free_bytes=lambda: 100 * GIB
    )

    assert deleted == 1
