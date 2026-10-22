"""Tests for CameraRecorder: pre-roll buffering and clip start/stop/rollover."""

from __future__ import annotations

from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from app.services.recording.packets import ClipStart, EncodedPacket
from app.services.recording.recorder import CameraRecorder

_NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
_FPS = 10
_GOP = 10  # one keyframe per second at _FPS


class FakeSink:
    def __init__(self, start: ClipStart) -> None:
        self.start = start
        self.packets: list[EncodedPacket] = []
        self.finished = False

    def write(self, packet: EncodedPacket) -> None:
        assert not self.finished
        self.packets.append(packet)

    def finish(self) -> None:
        self.finished = True


class SinkFactory:
    def __init__(self, refuse: bool = False) -> None:
        self.sinks: list[FakeSink] = []
        self.refuse = refuse
        self.calls = 0

    def __call__(self, start: ClipStart) -> FakeSink | None:
        self.calls += 1
        if self.refuse:
            return None
        sink = FakeSink(start)
        self.sinks.append(sink)
        return sink


def _packet(index: int, gop: int = _GOP) -> EncodedPacket:
    return EncodedPacket(
        data=bytes([index % 256]),
        pts=index,
        dts=index,
        time_base=Fraction(1, _FPS),
        is_keyframe=index % gop == 0,
        received_at=index / _FPS,
    )


def _opener(path: Path) -> None:
    return None


def _recorder(
    factory: SinkFactory, max_clip_seconds: float = 300.0, max_buffered: int = 1500
) -> CameraRecorder:
    recorder = CameraRecorder(
        "entrance",
        factory,
        pre_roll_seconds=3.0,
        max_clip_seconds=max_clip_seconds,
        max_buffered_packets=max_buffered,
        now=lambda: _NOW,
    )
    recorder.on_stream_opened(_opener)
    recorder.arm(True)
    return recorder


def _feed(recorder: CameraRecorder, indexes: range) -> None:
    for i in indexes:
        recorder.on_packet(_packet(i))


def test_does_not_record_until_a_deadline_is_set() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    _feed(recorder, range(100))
    assert factory.calls == 0


def test_clip_starts_with_pre_roll_from_a_keyframe() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    _feed(recorder, range(100))  # 10 s of video, t = 0.0 .. 9.9
    recorder.record_until(20.0)
    _feed(recorder, range(100, 101))  # t = 10.0

    [sink] = factory.sinks
    first = sink.packets[0]
    assert first.is_keyframe
    # Latest keyframe at least 3 s before t=10.0 is t=7.0 (index 70).
    assert first.pts == 70
    assert [p.pts for p in sink.packets] == list(range(70, 101))


def test_clip_start_time_is_the_first_buffered_packet() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    _feed(recorder, range(100))
    recorder.record_until(20.0)
    _feed(recorder, range(100, 101))

    # The first packet (t=7.0) is 3 s older than the triggering one (t=10.0).
    assert factory.sinks[0].start.started_at == datetime(
        2026, 10, 22, 11, 59, 57, tzinfo=UTC
    )
    assert factory.sinks[0].start.camera_name == "entrance"


def test_packets_after_start_go_to_the_open_clip_until_the_deadline() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    _feed(recorder, range(50))
    recorder.record_until(8.0)
    _feed(recorder, range(50, 100))

    [sink] = factory.sinks
    assert sink.finished
    # Stops on the first packet at/after the deadline (t=8.0 -> index 80).
    assert sink.packets[-1].pts == 79


def test_extending_the_deadline_keeps_the_same_clip() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(2.0)
    _feed(recorder, range(15))
    recorder.record_until(5.0)
    _feed(recorder, range(15, 60))

    [sink] = factory.sinks
    assert sink.packets[0].pts == 0
    assert sink.packets[-1].pts == 49


def test_waits_for_a_keyframe_when_nothing_is_buffered() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(100.0)
    _feed(recorder, range(5, 25))  # starts mid-GOP

    [sink] = factory.sinks
    assert sink.packets[0].pts == 10
    assert sink.packets[0].is_keyframe


def test_next_clip_does_not_replay_packets_of_the_previous_one() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(2.0)
    _feed(recorder, range(30))  # clip 1: 0..19
    recorder.record_until(10.0)
    _feed(recorder, range(30, 45))

    first, second = factory.sinks
    assert first.finished
    assert [p.pts for p in first.packets] == list(range(0, 20))
    # Its pre-roll starts with packets the first clip never got.
    assert second.packets[0].pts == 20


def test_long_events_roll_over_on_a_keyframe() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory, max_clip_seconds=2.0)
    recorder.record_until(100.0)
    _feed(recorder, range(55))

    first, second, third = factory.sinks[:3]
    assert first.finished and second.finished
    assert [p.pts for p in first.packets] == list(range(0, 20))
    assert [p.pts for p in second.packets] == list(range(20, 40))
    assert third.packets[0].pts == 40
    assert all(sink.packets[0].is_keyframe for sink in factory.sinks)


def test_disarming_stops_the_clip_and_drops_the_buffer() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(100.0)
    _feed(recorder, range(20))
    recorder.arm(False)
    _feed(recorder, range(20, 21))
    assert factory.sinks[0].finished

    recorder.arm(True)
    _feed(recorder, range(21, 35))
    # No pre-roll survived disarming: the new clip waits for keyframe 30.
    assert factory.sinks[1].packets[0].pts == 30


def test_stream_reopen_finishes_the_clip_and_clears_the_buffer() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(100.0)
    _feed(recorder, range(15))
    recorder.on_stream_opened(_opener)
    assert factory.sinks[0].finished

    _feed(recorder, range(15, 25))
    assert factory.sinks[1].packets[0].pts == 20


def test_stream_close_finishes_the_clip_and_blocks_new_ones() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(100.0)
    _feed(recorder, range(5))
    recorder.on_stream_closed()
    assert factory.sinks[0].finished

    _feed(recorder, range(10, 30))  # no opener until the stream reopens
    assert len(factory.sinks) == 1


def test_refused_clip_is_retried_on_the_next_keyframe_only() -> None:
    factory = SinkFactory(refuse=True)
    recorder = _recorder(factory)
    recorder.record_until(100.0)
    _feed(recorder, range(25))
    # Keyframes 0, 10 and 20 each try once; packets in between don't.
    assert factory.calls == 3

    factory.refuse = False
    _feed(recorder, range(25, 31))
    # Started on keyframe 30, with the 3 s pre-roll still buffered.
    assert factory.calls == 4
    assert factory.sinks[0].packets[0].pts == 0


def test_buffer_is_bounded_without_keyframes() -> None:
    factory = SinkFactory()
    recorder = CameraRecorder(
        "entrance", factory, max_buffered_packets=10, now=lambda: _NOW
    )
    recorder.on_stream_opened(_opener)
    recorder.arm(True)
    for i in range(30):
        recorder.on_packet(_packet(i, gop=1000))  # one keyframe, then none
    recorder.record_until(100.0)
    recorder.on_packet(_packet(30, gop=1000))
    # The oversized GOP was dropped, so there is nothing to start from.
    assert factory.sinks == []


def test_packets_before_the_first_keyframe_are_ignored() -> None:
    factory = SinkFactory()
    recorder = _recorder(factory)
    recorder.record_until(100.0)
    _feed(recorder, range(3, 9))
    assert factory.calls == 0
