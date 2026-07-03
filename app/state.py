"""Application-level singletons shared across routers."""

from __future__ import annotations

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.services.camera import RTSPCamera
from app.services.ptz import PTZService

cameras: dict[str, RTSPCamera] = {}
ptz_services: dict[str, PTZService] = {}
frame_buffers: dict[str, DoubleFrameBuffer] = {}
