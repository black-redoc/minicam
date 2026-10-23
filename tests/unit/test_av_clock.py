"""Tests for AudioClockAligner: audio moved onto the video clock."""

from __future__ import annotations

import dataclasses
from fractions import Fraction

import pytest

from app.services.av_clock import AudioClockAligner
from app.services.recording.packets import EncodedPacket, PacketKind

_VIDEO_TB = Fraction(1, 90000)
_AUDIO_TB = Fraction(1, 16000)


def _packet(
    kind: PacketKind, media_seconds: float, received_at: float
) -> EncodedPacket:
    time_base = _VIDEO_TB if kind == "video" else _AUDIO_TB
    ticks = round(media_seconds / time_base)
    return EncodedPacket(
        data=b"x",
        pts=ticks,
        dts=ticks,
        time_base=time_base,
        is_keyframe=True,
        received_at=received_at,
        kind=kind,
    )


def _seconds(packet: EncodedPacket) -> float:
    assert packet.pts is not None
    return float(packet.pts * packet.time_base)


def test_audio_is_shifted_by_the_measured_offset() -> None:
    # Video clock reads 5000 s and audio clock 120 s at the same instant,
    # with packets arriving up to 30 ms late.
    aligner = AudioClockAligner(calibration_seconds=1.0)
    for i in range(10):  # 0.9 s: still calibrating
        now = 100 + i * 0.1
        delay = 0.03 if i % 2 else 0.0
        assert aligner.align(_packet("video", 5000 + i * 0.1, now + delay)) is not None
        assert (
            aligner.align(_packet("audio", 120 + i * 0.1, now + 0.03 - delay)) is None
        )

    aligned = aligner.align(_packet("audio", 121.5, 101.5))
    assert aligned is not None
    # The quickest packets of each stream set the offset: 4880 s.
    assert _seconds(aligned) == pytest.approx(5001.5, abs=1e-3)
    assert aligned.dts == aligned.pts


def test_video_always_passes_unchanged() -> None:
    aligner = AudioClockAligner()
    packet = _packet("video", 10.0, 1.0)
    assert aligner.align(packet) is packet


def test_audio_waits_for_both_streams_before_aligning() -> None:
    aligner = AudioClockAligner(calibration_seconds=0.5)
    for i in range(10):
        assert aligner.align(_packet("audio", i * 0.1, i * 0.1)) is None


def test_synced_streams_get_no_shift() -> None:
    aligner = AudioClockAligner(calibration_seconds=0.0)
    aligner.align(_packet("video", 7.0, 1.0))
    aligned = aligner.align(_packet("audio", 7.0, 1.0))
    assert aligned is not None
    assert _seconds(aligned) == pytest.approx(7.0)


def test_packets_without_timestamps() -> None:
    aligner = AudioClockAligner(calibration_seconds=0.0)
    untimed_video = dataclasses.replace(_packet("video", 1.0, 1.0), pts=None, dts=None)
    assert aligner.align(untimed_video) is untimed_video
    aligner.align(_packet("video", 1.0, 1.0))
    aligner.align(_packet("audio", 3.0, 1.0))

    dts_only = dataclasses.replace(_packet("audio", 4.0, 2.0), pts=None)
    aligned = aligner.align(dts_only)
    assert aligned is not None
    # AAC has no reordering: the decode time becomes the presentation time.
    assert aligned.pts == aligned.dts
    assert aligned.dts is not None
    assert float(aligned.dts * aligned.time_base) == pytest.approx(2.0)

    untimed_audio = dataclasses.replace(dts_only, dts=None)
    assert aligner.align(untimed_audio) is untimed_audio


def _calibrated() -> AudioClockAligner:
    aligner = AudioClockAligner(calibration_seconds=0.0)
    aligner.align(_packet("video", 0.0, 0.0))
    return aligner


def _audio(ticks: int, duration: int | None = 1024) -> EncodedPacket:
    return dataclasses.replace(
        _packet("audio", 0.0, 0.0), pts=ticks, dts=ticks, duration=duration
    )


def test_rounded_camera_timestamps_become_exact_frames() -> None:
    aligner = _calibrated()
    # EZVIZ: 1024-sample frames stamped 1280, 640, 1280... ticks apart.
    stamps = [0, 1280, 1920, 3200, 3840, 5120]
    aligned = [aligner.align(_audio(t)) for t in stamps]
    assert [a.pts for a in aligned if a is not None] == [
        0,
        1024,
        2048,
        3072,
        4096,
        5120,
    ]


def test_a_jump_resyncs_to_the_camera_timestamp() -> None:
    aligner = _calibrated()
    aligner.align(_audio(0))
    # 0.5 s of lost audio: far beyond RESYNC_SECONDS, so trust the camera.
    resynced = aligner.align(_audio(9024))
    following = aligner.align(_audio(10200))
    assert resynced is not None and resynced.pts == 9024
    assert following is not None and following.pts == 10048


def test_without_durations_timestamps_are_only_shifted() -> None:
    aligner = _calibrated()
    first = aligner.align(_audio(0, duration=None))
    second = aligner.align(_audio(1280, duration=None))
    assert first is not None and first.pts == 0
    assert second is not None and second.pts == 1280
