"""JPEG encoding — converts RGB24 numpy arrays to JPEG bytes via libjpeg-turbo."""

from __future__ import annotations

from typing import cast

import numpy as np
import simplejpeg

JPEG_QUALITY = 85
# Viewers outside the LAN pay for every byte on the home uplink (and often on
# mobile data): a lower quality roughly halves the JPEG size.
WAN_JPEG_QUALITY = 70


def encode_jpeg(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    """Encode an RGB24 numpy array as JPEG bytes.

    Uses libjpeg-turbo via simplejpeg for low-latency encoding.

    Args:
        image: Array of shape (height, width, 3) in RGB24 format.
        quality: JPEG quality, 1-100.

    Returns:
        Raw JPEG bytes ready to send over the wire.
    """
    return cast(bytes, simplejpeg.encode_jpeg(image, quality=quality, colorspace="RGB"))
