"""Builds the active detection backend from config.toml settings.

Fail-safe by design: any misconfiguration (backend disabled, missing model
file, model load failure) logs a warning and returns None instead of
raising, so a system with no detection backend configured keeps streaming
exactly as it does today, with detection simply turned off.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.services.detection.base import DetectionBackend
from app.services.detection.onnx_backend import OnnxDetectionBackend
from app.settings import get_config

_ONNX_BACKEND_NAME = "onnx"

logger = logging.getLogger(__name__)


@lru_cache
def get_detection_backend() -> DetectionBackend | None:
    """Return the configured detection backend, or None if unavailable."""
    detection_config = get_config().detection

    if detection_config.backend != _ONNX_BACKEND_NAME:
        return None

    model_path = Path(detection_config.model_path)
    if not model_path.is_absolute() or not model_path.is_file():
        logger.warning(
            "Detection backend 'onnx' configured but model path %r is not "
            "an existing absolute file — detection disabled",
            detection_config.model_path,
        )
        return None

    try:
        return OnnxDetectionBackend(
            model_path=str(model_path),
            confidence_threshold=detection_config.confidence_threshold,
        )
    except Exception:
        logger.warning(
            "Failed to load detection backend 'onnx' from %r — detection disabled",
            detection_config.model_path,
            exc_info=True,
        )
        return None
