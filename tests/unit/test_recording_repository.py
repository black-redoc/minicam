"""Tests for SQLiteRecordingRepository."""

from __future__ import annotations

import aiosqlite
import pytest

from app.repositories.recording_repository import SQLiteRecordingRepository


@pytest.fixture
def repository(db_connection: aiosqlite.Connection) -> SQLiteRecordingRepository:
    return SQLiteRecordingRepository(db_connection)


async def _add(
    repository: SQLiteRecordingRepository, camera: str, name: str, size: int = 10
) -> int:
    recording = await repository.add(
        camera, name, "2026-10-22T12:00:00+00:00", 12.5, size, ("dog", "person")
    )
    return recording.id


async def test_add_returns_the_stored_row(
    repository: SQLiteRecordingRepository,
) -> None:
    recording = await repository.add(
        "entrance", "a.mp4", "2026-10-22T12:00:00+00:00", 12.5, 1024, ("person",)
    )
    assert recording.camera_name == "entrance"
    assert recording.file_name == "a.mp4"
    assert recording.duration_seconds == 12.5
    assert recording.size_bytes == 1024
    assert recording.classes == ("person",)
    assert recording.created_at
    assert await repository.get(recording.id) == recording


async def test_get_missing_returns_none(repository: SQLiteRecordingRepository) -> None:
    assert await repository.get(999) is None


async def test_list_filters_by_camera_newest_first(
    repository: SQLiteRecordingRepository,
) -> None:
    a = await _add(repository, "entrance", "a.mp4")
    await _add(repository, "patio", "b.mp4")
    c = await _add(repository, "entrance", "c.mp4")

    listed = await repository.list_for_cameras(["entrance"], None, 10)
    assert [r.id for r in listed] == [c, a]


async def test_list_paginates_with_before_id(
    repository: SQLiteRecordingRepository,
) -> None:
    ids = [await _add(repository, "entrance", f"{i}.mp4") for i in range(5)]

    first = await repository.list_for_cameras(["entrance"], None, 2)
    second = await repository.list_for_cameras(["entrance"], first[-1].id, 2)
    assert [r.id for r in first] == [ids[4], ids[3]]
    assert [r.id for r in second] == [ids[2], ids[1]]


async def test_list_without_cameras_is_empty(
    repository: SQLiteRecordingRepository,
) -> None:
    await _add(repository, "entrance", "a.mp4")
    assert await repository.list_for_cameras([], None, 10) == []


async def test_oldest_total_and_file_names(
    repository: SQLiteRecordingRepository,
) -> None:
    assert await repository.oldest() is None
    assert await repository.total_size_bytes() == 0
    first = await _add(repository, "entrance", "a.mp4", size=100)
    await _add(repository, "patio", "b.mp4", size=50)

    oldest = await repository.oldest()
    assert oldest is not None and oldest.id == first
    assert await repository.total_size_bytes() == 150
    assert await repository.list_file_names() == {"a.mp4", "b.mp4"}


async def test_delete_and_delete_by_file_name(
    repository: SQLiteRecordingRepository,
) -> None:
    a = await _add(repository, "entrance", "a.mp4")
    await _add(repository, "entrance", "b.mp4")

    assert await repository.delete(a) is True
    assert await repository.delete(a) is False
    assert await repository.delete_by_file_name("b.mp4") is True
    assert await repository.delete_by_file_name("b.mp4") is False
    assert await repository.list_file_names() == set()
