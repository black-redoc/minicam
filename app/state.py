"""Application-level singletons shared across routers."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.services.camera import RTSPCamera
from app.services.detection.worker import DetectionSnapshot
from app.services.ptz import PTZService

cameras: dict[str, RTSPCamera] = {}
ptz_services: dict[str, PTZService] = {}
frame_buffers: dict[str, DoubleFrameBuffer] = {}
detection_workers: dict[str, asyncio.Task[None]] = {}
detection_results: dict[str, DetectionSnapshot | None] = {}

# Dedicated pool for blocking ONVIF/SOAP calls, kept separate from the default
# executor so a burst of PTZ activity can never delay JPEG frame encoding.
ptz_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="ptz"
)
