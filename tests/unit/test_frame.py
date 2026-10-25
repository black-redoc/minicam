import threading
import time

import numpy as np
import pytest

from app.models.frame import Frame


def _make_frame(**overrides: object) -> Frame:
    defaults: dict[str, object] = {
        "image": np.zeros((480, 640, 3), dtype=np.uint8),
        "width": 640,
        "height": 480,
        "timestamp": 1.0,
        "sequence": 0,
    }
    defaults.update(overrides)
    return Frame(**defaults)  # type: ignore[arg-type]


class TestFrameCreation:
    def test_stores_width(self) -> None:
        frame = _make_frame(width=1280)
        assert frame.width == 1280

    def test_stores_height(self) -> None:
        frame = _make_frame(height=720)
        assert frame.height == 720

    def test_stores_timestamp(self) -> None:
        frame = _make_frame(timestamp=3.14)
        assert frame.timestamp == 3.14

    def test_stores_sequence(self) -> None:
        frame = _make_frame(sequence=42)
        assert frame.sequence == 42

    def test_image_shape_matches_dimensions(self) -> None:
        image = np.zeros((240, 320, 3), dtype=np.uint8)
        frame = Frame(image=image, width=320, height=240, timestamp=0.5, sequence=1)
        assert frame.image.shape == (240, 320, 3)


class TestFrameImmutability:
    def test_image_is_read_only(self) -> None:
        frame = _make_frame()
        with pytest.raises(ValueError, match="read-only"):
            frame.image[0, 0, 0] = 255

    def test_width_cannot_be_reassigned(self) -> None:
        frame = _make_frame()
        with pytest.raises(AttributeError):
            frame.width = 1280  # type: ignore[misc]

    def test_height_cannot_be_reassigned(self) -> None:
        frame = _make_frame()
        with pytest.raises(AttributeError):
            frame.height = 720  # type: ignore[misc]

    def test_timestamp_cannot_be_reassigned(self) -> None:
        frame = _make_frame()
        with pytest.raises(AttributeError):
            frame.timestamp = 99.0  # type: ignore[misc]

    def test_sequence_cannot_be_reassigned(self) -> None:
        frame = _make_frame()
        with pytest.raises(AttributeError):
            frame.sequence = 99  # type: ignore[misc]

    def test_image_reference_cannot_be_reassigned(self) -> None:
        frame = _make_frame()
        with pytest.raises(AttributeError):
            frame.image = np.ones((480, 640, 3), dtype=np.uint8)  # type: ignore[misc]


class TestLazyFrame:
    def test_convert_is_not_called_on_creation(self) -> None:
        calls: list[int] = []
        Frame.lazy(
            lambda: calls.append(1) or np.zeros((2, 2, 3), np.uint8),
            width=2,
            height=2,
            timestamp=0.0,
            sequence=0,
        )
        assert calls == []

    def test_image_is_converted_once(self) -> None:
        calls: list[int] = []

        def convert() -> np.ndarray:
            calls.append(1)
            return np.zeros((2, 2, 3), dtype=np.uint8)

        frame = Frame.lazy(convert, width=2, height=2, timestamp=0.0, sequence=0)
        assert frame.image is frame.image
        assert calls == [1]

    def test_lazy_image_is_read_only(self) -> None:
        frame = Frame.lazy(
            lambda: np.zeros((2, 2, 3), dtype=np.uint8),
            width=2,
            height=2,
            timestamp=0.0,
            sequence=0,
        )
        with pytest.raises(ValueError, match="read-only"):
            frame.image[0, 0, 0] = 255

    def test_concurrent_readers_convert_once(self) -> None:
        calls: list[int] = []

        def convert() -> np.ndarray:
            calls.append(1)
            time.sleep(0.01)  # widen the race window
            return np.zeros((2, 2, 3), dtype=np.uint8)

        frame = Frame.lazy(convert, width=2, height=2, timestamp=0.0, sequence=0)
        images: list[np.ndarray] = []
        threads = [
            threading.Thread(target=lambda: images.append(frame.image))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert calls == [1]
        assert all(image is images[0] for image in images)

    def test_convert_is_released_after_first_access(self) -> None:
        frame = Frame.lazy(
            lambda: np.zeros((2, 2, 3), dtype=np.uint8),
            width=2,
            height=2,
            timestamp=0.0,
            sequence=0,
        )
        _ = frame.image
        assert frame._convert is None
