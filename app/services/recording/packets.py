"""Thread-safe copies of encoded video packets and the clip-sink contract.

The capture thread hands every demuxed packet to a CameraRecorder before
decoding it. Instead of sharing PyAV packet objects across threads (muxing
takes ownership of a packet's buffer), each one is copied into an immutable
EncodedPacket: a few hundred KB/s of memcpy per camera, in exchange for a
pre-roll buffer and writer queue that hold plain Python data only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    import av

PacketKind = Literal["video", "audio"]

# Builds an MP4 output container whose video stream copies the codec
# parameters of the live input stream, or returns None once that input is
# gone (camera stopped or reconnecting). Supplied by RTSPCamera, which owns
# the input container and its locks.
OutputOpener = Callable[[Path], "av.container.OutputContainer | None"]


@dataclass(frozen=True, slots=True)
class EncodedPacket:
    """One compressed video or audio packet, detached from its PyAV container.

    Args:
        data: The packet payload as received (H.264 Annex B for RTSP).
        pts: Presentation timestamp in time_base units, if known.
        dts: Decoding timestamp in time_base units, if known.
        time_base: Unit of pts/dts in seconds.
        is_keyframe: True if a decoder can start from this packet.
        received_at: time.monotonic() when the capture thread demuxed it.
        kind: Which of the camera's streams it belongs to.
        duration: How long it plays, in time_base units, if known.
    """

    data: bytes
    pts: int | None
    dts: int | None
    time_base: Fraction
    is_keyframe: bool
    received_at: float
    kind: PacketKind = "video"
    duration: int | None = None

    @classmethod
    def from_av(
        cls,
        packet: av.Packet[Any],
        time_base: Fraction,
        received_at: float,
        kind: PacketKind = "video",
    ) -> EncodedPacket | None:
        """Copy a demuxed av.Packet, or None for an empty (flush) packet."""
        if packet.size == 0:
            return None
        return cls(
            data=bytes(packet),
            pts=packet.pts,
            dts=packet.dts,
            time_base=packet.time_base or time_base,
            is_keyframe=bool(packet.is_keyframe),
            received_at=received_at,
            kind=kind,
            duration=packet.duration or None,
        )


@dataclass(frozen=True, slots=True)
class ClipStart:
    """Where and when a new clip begins."""

    camera_name: str
    started_at: datetime
    opener: OutputOpener


class ClipSink(Protocol):
    """Receives the packets of one clip, in order, from the capture thread."""

    def write(self, packet: EncodedPacket) -> None: ...

    def finish(self) -> None: ...


ClipSinkFactory = Callable[[ClipStart], ClipSink | None]
