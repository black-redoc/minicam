"""Tests for LiveFeed: fan-out of one camera's fMP4 stream to its viewers."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.services.live import feed as feed_module
from app.services.live.feed import LiveFeed, LiveMessage, LiveSubscription
from app.services.live.fmp4 import Fmp4Segmenter, Fragment
from app.services.recording.packets import EncodedPacket
from tests.fixtures.live import AUDIO_INIT, FakeSegmenter
from tests.fixtures.live import INIT as _INIT
from tests.fixtures.live import open_feed as _open_feed
from tests.fixtures.live import packet as _packet


def _drain(subscription: LiveSubscription) -> list[LiveMessage]:
    messages: list[LiveMessage] = []
    while subscription._queue:
        messages.append(subscription._queue.popleft())
    subscription._queued_bytes = 0
    return messages


async def _settle() -> None:
    """Let call_soon_threadsafe deliveries run."""
    await asyncio.sleep(0)


def _payloads(messages: list[LiveMessage]) -> list[bytes]:
    return [m.data for m in messages]


async def test_fragments_start_at_the_first_keyframe() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    with feed.subscribe() as subscription:
        feed.on_packet(_packet(1))
        feed.on_packet(_packet(2, keyframe=True))
        feed.on_packet(_packet(3))
        await _settle()

        assert _drain(subscription) == [
            _INIT,
            Fragment(b"\x02", True),
            Fragment(b"\x03", False),
        ]
    assert feed.mime == _INIT.mime


async def test_new_viewer_starts_at_the_latest_keyframe() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    for n, keyframe in [(1, True), (2, False), (3, True), (4, False)]:
        feed.on_packet(_packet(n, keyframe))

    with feed.subscribe() as subscription:
        assert _payloads(_drain(subscription)) == [b"init", b"\x03", b"\x04"]
        feed.on_packet(_packet(5))
        await _settle()
        assert _payloads(_drain(subscription)) == [b"\x05"]


async def test_next_waits_for_a_message() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    with feed.subscribe() as subscription:
        waiter = asyncio.ensure_future(subscription.next())
        await _settle()
        assert not waiter.done()
        feed.on_packet(_packet(1, keyframe=True))
        assert await asyncio.wait_for(waiter, 1) == _INIT
        assert await subscription.next() == Fragment(b"\x01", True)


async def test_unsubscribed_viewer_gets_nothing_more() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    with feed.subscribe() as subscription:
        pass
    feed.on_packet(_packet(1, keyframe=True))
    await _settle()
    assert _drain(subscription) == []


async def test_reconnect_sends_a_new_init_and_drops_the_old_stream() -> None:
    feed = LiveFeed()
    first = _open_feed(feed)
    feed.on_packet(_packet(1, keyframe=True))
    with feed.subscribe() as subscription:
        feed.on_stream_closed()
        assert first.closed
        assert feed.mime is None

        _open_feed(feed)
        feed.on_packet(_packet(2, keyframe=True))
        await _settle()
        messages = _drain(subscription)
    # The pre-reconnect messages were cleared by the new init segment.
    assert messages == [_INIT, Fragment(b"\x02", True)]


async def test_viewer_without_init_waits_for_one() -> None:
    feed = LiveFeed()
    with feed.subscribe() as subscription:
        assert _drain(subscription) == []


async def test_slow_viewer_skips_to_the_next_keyframe() -> None:
    feed = LiveFeed(max_queued_bytes=len(b"init") + 3)
    _open_feed(feed)
    with feed.subscribe() as subscription:
        for n, keyframe in [(1, True), (2, False), (3, False), (4, False), (5, False)]:
            feed.on_packet(_packet(n, keyframe))
        feed.on_packet(_packet(6, keyframe=True))
        feed.on_packet(_packet(7))
        await _settle()

        # 4 overflowed the queue: the backlog was dropped (keeping init)
        # and nothing else was queued until keyframe 6.
        assert _payloads(_drain(subscription)) == [b"init", b"\x06", b"\x07"]


async def test_overflow_on_a_keyframe_keeps_the_keyframe() -> None:
    feed = LiveFeed(max_queued_bytes=len(b"init") + 2)
    _open_feed(feed)
    with feed.subscribe() as subscription:
        for n, keyframe in [(1, True), (2, False), (3, True)]:
            feed.on_packet(_packet(n, keyframe))
        await _settle()
        assert _payloads(_drain(subscription)) == [b"init", b"\x03"]


async def test_gop_without_keyframes_is_not_kept_for_new_viewers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feed_module, "_MAX_GOP_FRAGMENTS", 2)
    feed = LiveFeed()
    _open_feed(feed)
    for n, keyframe in [(1, True), (2, False), (3, False), (4, False)]:
        feed.on_packet(_packet(n, keyframe))

    with feed.subscribe() as subscription:
        assert _payloads(_drain(subscription)) == [b"init"]


def test_no_segmenter_until_the_stream_is_opened() -> None:
    feed = LiveFeed()
    feed.on_packet(_packet(1, keyframe=True))
    assert feed.mime is None


def test_opener_returning_none_disables_the_feed_until_reopened() -> None:
    feed = LiveFeed()
    opener = MagicMock(return_value=None)
    feed.on_stream_opened(opener)
    feed.on_packet(_packet(1, keyframe=True))
    feed.on_packet(_packet(2, keyframe=True))
    opener.assert_called_once()
    assert feed.mime is None


def test_opener_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    feed = LiveFeed()
    feed.on_stream_opened(MagicMock(side_effect=OSError("no output")))
    with caplog.at_level(logging.ERROR):
        feed.on_packet(_packet(1, keyframe=True))
    assert "Live stream unavailable: OSError: no output" in caplog.text
    assert feed.mime is None


def test_mux_error_stops_the_feed_until_reconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feed = LiveFeed()
    segmenter = _open_feed(feed)
    segmenter.fail_push = True
    with caplog.at_level(logging.ERROR):
        feed.on_packet(_packet(1, keyframe=True))
        feed.on_packet(_packet(2, keyframe=True))
    assert caplog.text.count("Live stream stopped: RuntimeError: mux failed") == 1
    assert segmenter.closed

    _open_feed(feed)
    feed.on_packet(_packet(3, keyframe=True))
    assert feed.mime == _INIT.mime


def test_close_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    feed = LiveFeed()
    segmenter = _open_feed(feed)
    feed.on_packet(_packet(1, keyframe=True))
    segmenter.fail_close = True
    with caplog.at_level(logging.WARNING):
        feed.on_stream_closed()
    assert "Could not close live stream: RuntimeError: close failed" in caplog.text


async def test_closed_loop_does_not_break_the_capture_thread() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    with feed.subscribe() as subscription:
        loop = MagicMock()
        loop.call_soon_threadsafe.side_effect = RuntimeError("loop closed")
        feed._video._loop = loop
        feed.on_packet(_packet(1, keyframe=True))  # must not raise
        loop.call_soon_threadsafe.assert_called()
    assert _drain(subscription) == []


async def test_audio_rendition_carries_audio_only_to_its_viewers() -> None:
    feed = LiveFeed()
    _open_feed(feed, audio=True)
    with feed.subscribe() as video_only, feed.subscribe(audio=True) as with_audio:
        # Every AAC packet is a keyframe: neither rendition may start on one.
        feed.on_packet(_packet(1, keyframe=True, kind="audio"))
        feed.on_packet(_packet(2, keyframe=True))
        feed.on_packet(_packet(3, keyframe=True, kind="audio"))
        feed.on_packet(_packet(4))
        await _settle()

        assert _payloads(_drain(video_only)) == [b"init", b"\x02", b"\x04"]
        assert _payloads(_drain(with_audio)) == [
            b"init+audio",
            b"\x02",
            b"\x03",
            b"\x04",
        ]
    assert feed.mime == _INIT.mime
    assert feed.audio_mime == AUDIO_INIT.mime


async def test_audio_fragments_are_not_gop_starts() -> None:
    feed = LiveFeed()
    _open_feed(feed, audio=True)
    feed.on_packet(_packet(1, keyframe=True))
    feed.on_packet(_packet(2, keyframe=False, kind="audio"))
    feed.on_packet(_packet(3))
    with feed.subscribe(audio=True) as subscription:
        assert _payloads(_drain(subscription)) == [
            b"init+audio",
            b"\x01",
            b"\x02",
            b"\x03",
        ]


def test_camera_without_audio_has_no_audio_mime() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    feed.on_packet(_packet(1, keyframe=True))
    feed.on_packet(_packet(2, kind="audio"))
    assert feed.mime == _INIT.mime
    assert feed.audio_mime is None


def _packet_at(n: int, at: float, keyframe: bool = False) -> EncodedPacket:
    return dataclasses.replace(_packet(n, keyframe), received_at=at)


def test_rendition_without_viewers_stops_once_idle_but_keeps_its_mime() -> None:
    feed = LiveFeed()
    segmenter = _open_feed(feed)
    feed.on_packet(_packet_at(1, 0.0, keyframe=True))
    feed.on_packet(_packet_at(2, feed_module._IDLE_STOP_SECONDS - 1))
    assert not segmenter.closed

    feed.on_packet(_packet_at(3, feed_module._IDLE_STOP_SECONDS))
    assert segmenter.closed
    assert feed.mime == _INIT.mime


async def test_viewer_restarts_a_stopped_rendition_at_the_next_keyframe() -> None:
    feed = LiveFeed()
    segmenters = [FakeSegmenter(), FakeSegmenter()]
    opener = MagicMock(side_effect=segmenters)
    feed.on_stream_opened(opener)
    feed.on_packet(_packet_at(1, 0.0, keyframe=True))
    feed.on_packet(_packet_at(2, feed_module._IDLE_STOP_SECONDS))
    assert segmenters[0].closed

    with feed.subscribe() as subscription:
        assert _drain(subscription) == []  # nothing buffered while stopped
        feed.on_packet(_packet_at(3, 20.0))
        feed.on_packet(_packet_at(4, 21.0, keyframe=True))
        await _settle()
        assert _drain(subscription) == [_INIT, Fragment(b"\x04", True)]
    assert opener.call_count == 2


async def test_rendition_stops_once_its_last_viewer_has_been_gone_a_while() -> None:
    feed = LiveFeed()
    segmenter = _open_feed(feed)
    with feed.subscribe():
        feed.on_packet(_packet_at(1, 0.0, keyframe=True))
        feed.on_packet(_packet_at(2, 100.0))
    feed.on_packet(_packet_at(3, 101.0))
    assert not segmenter.closed  # the viewer may be reconnecting
    feed.on_packet(_packet_at(4, 101.0 + feed_module._IDLE_STOP_SECONDS))
    assert segmenter.closed


async def test_audio_rendition_stops_while_video_only_keeps_its_viewer() -> None:
    feed = LiveFeed()
    audio_segmenter = FakeSegmenter(AUDIO_INIT)
    feed.on_stream_opened(
        lambda: cast(Fmp4Segmenter, video_segmenter),
        lambda: cast(Fmp4Segmenter, audio_segmenter),
    )
    video_segmenter = FakeSegmenter()
    with feed.subscribe():
        feed.on_packet(_packet_at(1, 0.0, keyframe=True))
        feed.on_packet(_packet_at(2, feed_module._IDLE_STOP_SECONDS))
    assert audio_segmenter.closed
    assert not video_segmenter.closed
    assert feed.audio_mime == AUDIO_INIT.mime


def test_stream_close_forgets_the_mime_of_a_stopped_rendition() -> None:
    feed = LiveFeed()
    _open_feed(feed)
    feed.on_packet(_packet_at(1, 0.0, keyframe=True))
    feed.on_packet(_packet_at(2, feed_module._IDLE_STOP_SECONDS))
    feed.on_stream_closed()
    assert feed.mime is None
