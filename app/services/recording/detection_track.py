"""Detection boxes of a recorded clip, kept as a sidecar JSON file (ADR 0012).

Clips are a remux of the camera's packets, so boxes can't be drawn into
them without a CPU re-encode. Instead the recording service keeps each
camera's recent detection snapshots in a DetectionTimeline and, when a clip
is registered, writes the ones inside it next to the clip:

    {"version": 1, "width": W, "height": H,
     "frames": [{"t": seconds from clip start, "boxes": [...]}]}

Boxes use the same shape as the live {"type": "detections"} messages, in
frame pixels. A frame with no boxes clears the ones drawn before it.
Players draw the frame whose t is the latest one not after the playback
position.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.detection.draw import box_payload
from app.services.detection.worker import DetectionSnapshot

logger = logging.getLogger(__name__)

TRACK_VERSION = 1
SIDECAR_SUFFIX = ".detections.json"
# Long enough to cover the longest clip (recorder.MAX_CLIP_SECONDS) plus its
# pre-roll and the time the writer needs to finish it.
_HISTORY_SECONDS = 600.0
# A snapshot analysed shortly before the clip's first packet still shows
# what is on screen when the clip starts.
_LEAD_IN_SECONDS = 1.0
_TIME_DECIMALS = 2


def empty_track() -> dict[str, Any]:
    return {"version": TRACK_VERSION, "width": 0, "height": 0, "frames": []}


@dataclass(frozen=True, slots=True)
class _Entry:
    at: float
    width: int
    height: int
    boxes: tuple[dict[str, Any], ...]


class DetectionTimeline:
    """One camera's recent detection snapshots, by time.monotonic()."""

    def __init__(self, history_seconds: float = _HISTORY_SECONDS) -> None:
        self._history_seconds = history_seconds
        self._entries: deque[_Entry] = deque()

    def add(self, snapshot: DetectionSnapshot) -> None:
        at = snapshot.captured_at
        if at is None:
            return  # built outside the worker: no time to place it at
        boxes = tuple(box_payload(d) for d in snapshot.detections)
        last = self._entries[-1] if self._entries else None
        if last is not None and not boxes and not last.boxes:
            return  # still nothing on screen: the last entry already says so
        self._entries.append(
            _Entry(at, snapshot.frame_width, snapshot.frame_height, boxes)
        )
        while self._entries and self._entries[0].at < at - self._history_seconds:
            self._entries.popleft()

    def clear(self) -> None:
        self._entries.clear()

    def track(self, start: float, duration: float) -> dict[str, Any]:
        """The boxes inside [start, start + duration], timed from start."""
        end = start + duration
        lead_in: _Entry | None = None
        inside: list[_Entry] = []
        for entry in self._entries:
            if entry.at < start:
                lead_in = entry if start - entry.at <= _LEAD_IN_SECONDS else None
            elif entry.at <= end:
                inside.append(entry)
        entries = ([lead_in] if lead_in is not None else []) + inside
        if not any(entry.boxes for entry in entries):
            return empty_track()
        sized = next((e for e in entries if e.width > 0 and e.height > 0), None)
        return {
            "version": TRACK_VERSION,
            "width": sized.width if sized is not None else 0,
            "height": sized.height if sized is not None else 0,
            "frames": [
                {
                    "t": round(max(0.0, entry.at - start), _TIME_DECIMALS),
                    "boxes": list(entry.boxes),
                }
                for entry in entries
            ],
        }


def sidecar_path(clip_path: Path) -> Path:
    """`<name>.mp4` → `<name>.detections.json`, in the same directory."""
    return clip_path.with_name(clip_path.stem + SIDECAR_SUFFIX)


def clip_name_for_sidecar(file_name: str) -> str | None:
    """The clip file a sidecar belongs to, or None if it isn't a sidecar."""
    if not file_name.endswith(SIDECAR_SUFFIX):
        return None
    return file_name[: -len(SIDECAR_SUFFIX)] + ".mp4"


def write_track(clip_path: Path, track: dict[str, Any]) -> None:
    """Write the clip's sidecar atomically; nothing is written for no boxes."""
    if not track["frames"]:
        return
    path = sidecar_path(clip_path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(track, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


def read_track(clip_path: Path) -> dict[str, Any]:
    """The clip's sidecar, or an empty track if it has none or it is unreadable."""
    path = sidecar_path(clip_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_track()
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable detection track %s: %s", path.name, exc)
        return empty_track()
    if not isinstance(data, dict) or data.get("version") != TRACK_VERSION:
        return empty_track()
    return data


def delete_track(clip_path: Path) -> None:
    sidecar_path(clip_path).unlink(missing_ok=True)
