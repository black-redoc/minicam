"""Disk budget for event recordings: selectable limits and retention.

A storage limit is offered only if the disk can actually hold it: the
space already used by recordings counts as available (it is part of the
budget), and RESERVE_BYTES always stays free for the OS, the database and
config writes.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.models.config import RECORDING_STORAGE_OPTIONS_GB
from app.repositories.recording_repository import RecordingRepository

logger = logging.getLogger(__name__)

GIB = 1024**3
RESERVE_BYTES = 1 * GIB


@dataclass(frozen=True, slots=True)
class StorageOption:
    gb: int
    available: bool


def free_bytes(directory: Path) -> int:
    """Free space on the filesystem holding directory (created if missing)."""
    directory.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(directory).free


def storage_options(free: int, used_by_recordings: int) -> list[StorageOption]:
    budget = free + used_by_recordings - RESERVE_BYTES
    return [
        StorageOption(gb=gb, available=gb * GIB <= budget)
        for gb in RECORDING_STORAGE_OPTIONS_GB
    ]


def is_option_available(gb: int, free: int, used_by_recordings: int) -> bool:
    return any(
        option.gb == gb and option.available
        for option in storage_options(free, used_by_recordings)
    )


async def enforce_retention(
    repository: RecordingRepository,
    recordings_dir: Path,
    limit_bytes: int,
    get_free_bytes: Callable[[], int],
) -> int:
    """Delete the oldest clips until under limit_bytes with RESERVE_BYTES free.

    Returns how many clips were deleted.
    """
    deleted = 0
    total = await repository.total_size_bytes()
    while (
        total > limit_bytes or await asyncio.to_thread(get_free_bytes) < RESERVE_BYTES
    ):
        oldest = await repository.oldest()
        if oldest is None:
            break
        await asyncio.to_thread(
            (recordings_dir / oldest.file_name).unlink, missing_ok=True
        )
        await repository.delete(oldest.id)
        total -= oldest.size_bytes
        deleted += 1
    if deleted:
        logger.info("Recording retention deleted %d old clip(s)", deleted)
    return deleted
