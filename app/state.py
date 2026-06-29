"""Application-level singletons shared across routers.

Instances are created here; lifespan in main.py starts/stops them.
"""
from __future__ import annotations

from app.buffers.double_frame_buffer import DoubleFrameBuffer
from app.services.camera import RTSPCamera
from app.services.ptz import PTZService

frame_buffer: DoubleFrameBuffer = DoubleFrameBuffer()
camera: RTSPCamera | None = None
ptz: PTZService | None = None
