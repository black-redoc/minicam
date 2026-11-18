"""Tests for app.services.recording.detection_track (ADR 0012)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot
from app.services.recording import detection_track
from app.services.recording.detection_track import DetectionTimeline


def _snapshot(at: float | None, *classes: str) -> DetectionSnapshot:
    return DetectionSnapshot(
        detections=[Detection(c, 0.912, 10, 20, 110, 220) for c in classes],
        sequence=0,
        captured_at=at,
        frame_width=640,
        frame_height=360,
    )


def test_track_times_boxes_from_the_clip_start() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(101.0, "person"))
    timeline.add(_snapshot(103.5))

    track = timeline.track(start=100.0, duration=10.0)

    assert track == {
        "version": 1,
        "width": 640,
        "height": 360,
        "frames": [
            {
                "t": 1.0,
                "boxes": [
                    {
                        "label": "person",
                        "confidence": 0.91,
                        "color": "#ff0000",
                        "x1": 10,
                        "y1": 20,
                        "x2": 110,
                        "y2": 220,
                    }
                ],
            },
            {"t": 3.5, "boxes": []},
        ],
    }


def test_track_leaves_out_snapshots_outside_the_clip() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(90.0, "person"))
    timeline.add(_snapshot(105.0, "dog"))
    timeline.add(_snapshot(120.0, "car"))

    frames = timeline.track(start=100.0, duration=10.0)["frames"]

    assert [(f["t"], f["boxes"][0]["label"]) for f in frames] == [(5.0, "dog")]


def test_a_snapshot_just_before_the_clip_starts_it_at_zero() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(99.5, "person"))

    frames = timeline.track(start=100.0, duration=10.0)["frames"]

    assert [f["t"] for f in frames] == [0.0]


def test_a_track_with_no_boxes_is_empty() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(101.0))

    assert timeline.track(100.0, 10.0) == detection_track.empty_track()


def test_repeated_empty_snapshots_are_kept_once() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(101.0, "person"))
    timeline.add(_snapshot(102.0))
    timeline.add(_snapshot(103.0))

    assert [f["t"] for f in timeline.track(100.0, 10.0)["frames"]] == [1.0, 2.0]


def test_snapshots_without_a_capture_time_are_ignored() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(None, "person"))

    assert timeline.track(0.0, 1e9)["frames"] == []


def test_old_snapshots_are_forgotten() -> None:
    timeline = DetectionTimeline(history_seconds=10.0)
    timeline.add(_snapshot(100.0, "person"))
    timeline.add(_snapshot(120.0, "dog"))

    labels = [f["boxes"][0]["label"] for f in timeline.track(0.0, 1e9)["frames"]]
    assert labels == ["dog"]


def test_clear_drops_everything() -> None:
    timeline = DetectionTimeline()
    timeline.add(_snapshot(101.0, "person"))
    timeline.clear()

    assert timeline.track(100.0, 10.0)["frames"] == []


def test_sidecar_names() -> None:
    clip = Path("/r/20261118T120000Z-ab12cd34.mp4")
    assert detection_track.sidecar_path(clip) == Path(
        "/r/20261118T120000Z-ab12cd34.detections.json"
    )
    assert detection_track.clip_name_for_sidecar("x.detections.json") == "x.mp4"
    assert detection_track.clip_name_for_sidecar("x.mp4") is None


def test_write_read_and_delete_round_trip(tmp_path: Path) -> None:
    clip = tmp_path / "a.mp4"
    timeline = DetectionTimeline()
    timeline.add(_snapshot(101.0, "person"))
    track = timeline.track(100.0, 10.0)

    detection_track.write_track(clip, track)

    assert detection_track.read_track(clip) == track
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.detections.json"]
    detection_track.delete_track(clip)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_track_writes_nothing(tmp_path: Path) -> None:
    detection_track.write_track(tmp_path / "a.mp4", detection_track.empty_track())

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content", ["not json", json.dumps([1]), json.dumps({"version": 99})]
)
def test_unreadable_or_unknown_tracks_read_as_empty(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "a.detections.json").write_text(content)

    assert detection_track.read_track(tmp_path / "a.mp4") == (
        detection_track.empty_track()
    )


def test_a_missing_track_reads_as_empty(tmp_path: Path) -> None:
    assert detection_track.read_track(tmp_path / "a.mp4") == (
        detection_track.empty_track()
    )
