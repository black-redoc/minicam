"""Application-level singletons shared across routers."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from app.buffers.frame_buffer import FrameBuffer
from app.services.camera import RTSPCamera
from app.services.detection.worker import DetectionSnapshot
from app.services.live.feed import LiveFeed
from app.services.ptz import PTZService
from app.services.usage import UsageMeter

if TYPE_CHECKING:
    from app.services.central import CentralRuntime
    from app.services.notifications.mobile_push import MobilePushRelay

cameras: dict[str, RTSPCamera] = {}
ptz_services: dict[str, PTZService] = {}
frame_buffers: dict[str, FrameBuffer] = {}
live_feeds: dict[str, LiveFeed] = {}
detection_workers: dict[str, asyncio.Task[None]] = {}
detection_results: dict[str, DetectionSnapshot | None] = {}
# Central mode only (minicam-admin ADR 0008): WAN stream bytes not yet in SQLite.
usage_meter: UsageMeter | None = None
# Entitlement sync + usage loops; started at boot or when the app pairs (ADR 0009).
central_runtime: CentralRuntime | None = None
# The dashboard's push relay client, only while central mode runs (ADR 0011).
mobile_push_relay: MobilePushRelay | None = None

# Dedicated pool for blocking ONVIF/SOAP calls, kept separate from the default
# executor so a burst of PTZ activity can never delay JPEG frame encoding.
ptz_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="ptz"
)
