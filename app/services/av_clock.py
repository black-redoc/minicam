"""Puts a camera's audio timestamps on its video clock."""

from __future__ import annotations

import dataclasses
from fractions import Fraction

from app.services.recording.packets import EncodedPacket, PacketKind

CALIBRATION_SECONDS = 1.0
RESYNC_SECONDS = 0.2


class AudioClockAligner:
    """Shifts audio timestamps onto the video clock, measured by arrival time.

    Many cameras (EZVIZ among them) start their audio and video RTP clocks at
    unrelated values and send no usable RTCP sender reports, so FFmpeg's
    audio and video timestamps differ by an arbitrary offset that changes
    with every connection. Both clocks do run at real time, so the offset is
    measured instead: per stream, media time minus arrival time is largest
    for the packets that arrived quickest. Once both streams have been seen
    for CALIBRATION_SECONDS, the difference of those maxima is the offset,
    fixed for the rest of the connection. Cameras whose timestamps are
    already in sync simply measure an offset close to zero.

    Audio is dropped until the offset is known; video always passes through
    unchanged and undelayed. One instance per RTSP connection.

    Audio timestamps are also made exact: cameras round them (EZVIZ sends
    AAC frames of 64 ms stamped 80, 40, 80... ms apart), which leaves gaps
    and overlaps between frames that stall a browser's playback. Each frame
    is stamped right after the previous one instead, unless the camera's
    own timestamp is more than RESYNC_SECONDS away (lost packets): then the
    camera's is used and the run starts over from it.
    """

    def __init__(self, calibration_seconds: float = CALIBRATION_SECONDS) -> None:
        self._calibration_seconds = calibration_seconds
        self._lead: dict[PacketKind, float] = {}
        self._first_received_at: float | None = None
        # Seconds to subtract from audio timestamps, once measured.
        self._shift: Fraction | None = None
        # Where the next audio frame starts, in its time base, on the video clock.
        self._next_audio: int | None = None

    def align(self, packet: EncodedPacket) -> EncodedPacket | None:
        """packet on the video clock, or None for audio not yet alignable."""
        timestamp = packet.pts if packet.pts is not None else packet.dts
        if self._shift is None and timestamp is not None:
            self._measure(packet, timestamp)
        if packet.kind == "video":
            return packet
        if self._shift is None:
            return None
        if timestamp is None:
            return packet
        aligned = timestamp - round(self._shift / packet.time_base)
        expected = self._next_audio
        if expected is not None and abs(aligned - expected) * packet.time_base <= (
            RESYNC_SECONDS
        ):
            aligned = expected
        self._next_audio = (
            aligned + packet.duration if packet.duration is not None else None
        )
        # AAC has no frame reordering: decode and presentation times match.
        return dataclasses.replace(packet, pts=aligned, dts=aligned)

    def _measure(self, packet: EncodedPacket, timestamp: int) -> None:
        lead = float(timestamp * packet.time_base) - packet.received_at
        self._lead[packet.kind] = max(lead, self._lead.get(packet.kind, lead))
        if self._first_received_at is None:
            self._first_received_at = packet.received_at
        elapsed = packet.received_at - self._first_received_at
        if len(self._lead) == 2 and elapsed >= self._calibration_seconds:
            self._shift = Fraction(self._lead["audio"] - self._lead["video"])
