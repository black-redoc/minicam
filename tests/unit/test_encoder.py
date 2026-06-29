import numpy as np
import pytest

from app.services.encoder import encode_jpeg


class TestEncodeJpeg:
    def test_returns_bytes(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        result = encode_jpeg(image)
        assert isinstance(result, bytes)

    def test_jpeg_magic_bytes(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        result = encode_jpeg(image)
        assert result[:2] == b"\xff\xd8"

    def test_non_empty_output(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        result = encode_jpeg(image)
        assert len(result) > 0

    def test_different_content_produces_different_output(self) -> None:
        black = np.zeros((480, 640, 3), dtype=np.uint8)
        white = np.full((480, 640, 3), 255, dtype=np.uint8)
        assert encode_jpeg(black) != encode_jpeg(white)

    def test_small_frame(self) -> None:
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        result = encode_jpeg(image)
        assert result[:2] == b"\xff\xd8"

    def test_read_only_array_is_accepted(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        image.flags.writeable = False
        result = encode_jpeg(image)
        assert isinstance(result, bytes)
