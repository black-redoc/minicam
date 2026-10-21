"""Tests for app.services.detection.onnx_backend.

InferenceSession is always mocked here — these tests exercise the
pre/post-processing math with synthetic tensors we control, never a real
model file.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.services.detection import coco_classes
from app.services.detection.onnx_backend import (
    MODEL_INPUT_SIZE,
    OnnxDetectionBackend,
    _cxcywh_to_xyxy,
    _decode_output,
    _preprocess,
    _resize_nearest,
    _undo_letterbox,
)

PERSON_CLASS_INDEX = coco_classes.COCO_CLASSES.index("person")
CAT_CLASS_INDEX = coco_classes.COCO_CLASSES.index("cat")
NUM_CLASSES = len(coco_classes.COCO_CLASSES)
NUM_ANCHORS = 8400


def _make_raw_output(
    candidates: list[tuple[float, float, float, float, int, float]],
) -> np.ndarray:
    """Build a synthetic (1, 84, 8400) YOLOv8 raw output tensor.

    Each candidate is (cx, cy, w, h, class_index, class_score); every other
    anchor is left at all-zero scores so it is filtered out by the
    confidence threshold during decoding.
    """
    raw = np.zeros((1, 4 + NUM_CLASSES, NUM_ANCHORS), dtype=np.float32)
    for i, (cx, cy, w, h, class_index, score) in enumerate(candidates):
        raw[0, 0, i] = cx
        raw[0, 1, i] = cy
        raw[0, 2, i] = w
        raw[0, 3, i] = h
        raw[0, 4 + class_index, i] = score
    return raw


class TestResizeNearest:
    def test_preserves_shape(self) -> None:
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        result = _resize_nearest(image, new_width=4, new_height=4)
        assert result.shape == (4, 4, 3)

    def test_upscaling_preserves_dtype(self) -> None:
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        result = _resize_nearest(image, new_width=8, new_height=8)
        assert result.dtype == np.uint8
        assert result.shape == (8, 8, 3)

    def test_nearest_sampling_picks_original_pixels(self) -> None:
        image = np.arange(16, dtype=np.uint8).reshape(4, 4, 1).repeat(3, axis=2)
        result = _resize_nearest(image, new_width=2, new_height=2)
        # Every sampled pixel must come from the original image.
        original_values = set(image[:, :, 0].flatten().tolist())
        for value in result[:, :, 0].flatten().tolist():
            assert value in original_values


class TestCxcywhToXyxy:
    def test_converts_single_box(self) -> None:
        boxes = np.array([[100.0, 100.0, 50.0, 50.0]], dtype=np.float32)
        result = _cxcywh_to_xyxy(boxes)
        assert result.tolist() == [[75.0, 75.0, 125.0, 125.0]]

    def test_converts_multiple_boxes(self) -> None:
        boxes = np.array(
            [[100.0, 100.0, 50.0, 50.0], [200.0, 300.0, 20.0, 40.0]], dtype=np.float32
        )
        result = _cxcywh_to_xyxy(boxes)
        assert result.tolist() == [
            [75.0, 75.0, 125.0, 125.0],
            [190.0, 280.0, 210.0, 320.0],
        ]


class TestPreprocess:
    def test_output_tensor_shape(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        tensor, _, _, _ = _preprocess(image)
        assert tensor.shape == (1, 3, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE)

    def test_output_dtype_and_range(self) -> None:
        image = np.full((480, 640, 3), 255, dtype=np.uint8)
        tensor, _, _, _ = _preprocess(image)
        assert tensor.dtype == np.float32
        assert tensor.max() <= 1.0
        assert tensor.min() >= 0.0

    def test_wide_image_pads_vertically(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        _, scale, pad_x, pad_y = _preprocess(image)
        assert scale == pytest.approx(1.0)
        assert pad_x == pytest.approx(0.0)
        assert pad_y == pytest.approx(80.0)

    def test_square_image_has_no_padding(self) -> None:
        image = np.zeros((640, 640, 3), dtype=np.uint8)
        _, scale, pad_x, pad_y = _preprocess(image)
        assert scale == pytest.approx(1.0)
        assert pad_x == pytest.approx(0.0)
        assert pad_y == pytest.approx(0.0)


class TestDecodeOutput:
    def test_filters_low_confidence_candidates(self) -> None:
        raw = _make_raw_output([(320.0, 320.0, 50.0, 50.0, PERSON_CLASS_INDEX, 0.1)])
        boxes, scores, class_ids = _decode_output(raw, confidence_threshold=0.5)
        assert boxes.shape == (0, 4)
        assert scores.shape == (0,)
        assert class_ids.shape == (0,)

    def test_keeps_high_confidence_candidate(self) -> None:
        raw = _make_raw_output([(100.0, 100.0, 50.0, 50.0, PERSON_CLASS_INDEX, 0.9)])
        boxes, scores, class_ids = _decode_output(raw, confidence_threshold=0.5)
        assert boxes.tolist() == [[75.0, 75.0, 125.0, 125.0]]
        assert scores.tolist() == pytest.approx([0.9])
        assert class_ids.tolist() == [PERSON_CLASS_INDEX]

    def test_picks_winning_class_per_anchor(self) -> None:
        raw = _make_raw_output([(100.0, 100.0, 50.0, 50.0, CAT_CLASS_INDEX, 0.8)])
        _, _, class_ids = _decode_output(raw, confidence_threshold=0.5)
        assert class_ids.tolist() == [CAT_CLASS_INDEX]


class TestUndoLetterbox:
    def test_no_padding_no_scaling(self) -> None:
        boxes = np.array([[10.0, 20.0, 30.0, 40.0]], dtype=np.float32)
        result = _undo_letterbox(
            boxes, scale=1.0, pad_x=0.0, pad_y=0.0, width=640, height=640
        )
        assert result.tolist() == [[10.0, 20.0, 30.0, 40.0]]

    def test_removes_vertical_padding(self) -> None:
        boxes = np.array([[10.0, 90.0, 110.0, 190.0]], dtype=np.float32)
        result = _undo_letterbox(
            boxes, scale=1.0, pad_x=0.0, pad_y=80.0, width=640, height=480
        )
        assert result.tolist() == [[10.0, 10.0, 110.0, 110.0]]

    def test_clips_to_image_bounds(self) -> None:
        boxes = np.array([[-50.0, -50.0, 700.0, 700.0]], dtype=np.float32)
        result = _undo_letterbox(
            boxes, scale=1.0, pad_x=0.0, pad_y=0.0, width=640, height=480
        )
        x1, y1, x2, y2 = result[0]
        assert x1 == 0.0
        assert y1 == 0.0
        assert x2 == 639.0
        assert y2 == 479.0


def _make_backend_with_fake_session(raw_output: np.ndarray) -> OnnxDetectionBackend:
    """Build an OnnxDetectionBackend whose InferenceSession is fully mocked."""
    fake_input = MagicMock()
    fake_input.name = "images"
    fake_session = MagicMock()
    fake_session.get_inputs.return_value = [fake_input]
    fake_session.run.return_value = [raw_output]

    with patch(
        "app.services.detection.onnx_backend.ort.InferenceSession",
        return_value=fake_session,
    ):
        return OnnxDetectionBackend(
            model_path="/fake/model.onnx", confidence_threshold=0.5
        )


class TestOnnxDetectionBackendConstruction:
    def test_creates_session_with_cpu_provider(self) -> None:
        fake_input = MagicMock()
        fake_input.name = "images"
        fake_session = MagicMock()
        fake_session.get_inputs.return_value = [fake_input]

        with patch(
            "app.services.detection.onnx_backend.ort.InferenceSession",
            return_value=fake_session,
        ) as mock_session_cls:
            OnnxDetectionBackend(
                model_path="/fake/model.onnx", confidence_threshold=0.5
            )

        mock_session_cls.assert_called_once_with(
            "/fake/model.onnx", providers=["CPUExecutionProvider"]
        )

    def test_available_classes_is_coco(self) -> None:
        backend = _make_backend_with_fake_session(_make_raw_output([]))
        assert backend.available_classes == coco_classes.COCO_CLASSES


class TestOnnxDetectionBackendDetect:
    def test_detects_person_in_allowed_classes(self) -> None:
        raw = _make_raw_output([(100.0, 100.0, 50.0, 50.0, PERSON_CLASS_INDEX, 0.9)])
        backend = _make_backend_with_fake_session(raw)
        image = np.zeros((640, 640, 3), dtype=np.uint8)

        detections = backend.detect(image, allowed_classes=frozenset({"person"}))

        assert len(detections) == 1
        detection = detections[0]
        assert detection.class_name == "person"
        assert detection.confidence == pytest.approx(0.9)
        assert (detection.x1, detection.y1, detection.x2, detection.y2) == (
            75,
            75,
            125,
            125,
        )

    def test_filters_out_classes_not_allowed(self) -> None:
        raw = _make_raw_output([(100.0, 100.0, 50.0, 50.0, PERSON_CLASS_INDEX, 0.9)])
        backend = _make_backend_with_fake_session(raw)
        image = np.zeros((640, 640, 3), dtype=np.uint8)

        detections = backend.detect(image, allowed_classes=frozenset({"cat"}))

        assert detections == []

    def test_no_candidates_above_threshold_returns_empty(self) -> None:
        raw = _make_raw_output([])
        backend = _make_backend_with_fake_session(raw)
        image = np.zeros((640, 640, 3), dtype=np.uint8)

        detections = backend.detect(image, allowed_classes=frozenset({"person"}))

        assert detections == []

    def test_suppresses_overlapping_duplicate_detections(self) -> None:
        raw = _make_raw_output(
            [
                (100.0, 100.0, 50.0, 50.0, PERSON_CLASS_INDEX, 0.9),
                (102.0, 102.0, 50.0, 50.0, PERSON_CLASS_INDEX, 0.8),
            ]
        )
        backend = _make_backend_with_fake_session(raw)
        image = np.zeros((640, 640, 3), dtype=np.uint8)

        detections = backend.detect(image, allowed_classes=frozenset({"person"}))

        assert len(detections) == 1
        assert detections[0].confidence == pytest.approx(0.9)
