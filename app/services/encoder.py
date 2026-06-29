"""JPEG encoding — converts RGB24 numpy arrays to JPEG bytes via libjpeg-turbo."""
from __future__ import annotations

import numpy as np
import simplejpeg

JPEG_QUALITY = 85


def encode_jpeg(image: np.ndarray) -> bytes:
    """Encode an RGB24 numpy array as JPEG bytes.

    Uses libjpeg-turbo via simplejpeg for low-latency encoding.

    Args:
        image: Array of shape (height, width, 3) in RGB24 format.

    Returns:
        Raw JPEG bytes ready to send over the wire.
    """
    return simplejpeg.encode_jpeg(image, quality=JPEG_QUALITY, colorspace="RGB")
