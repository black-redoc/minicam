"""Tests for app.services.detection.nms."""

from __future__ import annotations

import numpy as np

from app.services.detection.nms import non_max_suppression


class TestNonMaxSuppression:
    def test_empty_input_returns_empty(self) -> None:
        boxes = np.empty((0, 4), dtype=np.float32)
        scores = np.empty((0,), dtype=np.float32)
        result = non_max_suppression(boxes, scores)
        assert result.shape == (0,)

    def test_single_box_is_kept(self) -> None:
        boxes = np.array([[10.0, 10.0, 50.0, 50.0]], dtype=np.float32)
        scores = np.array([0.9], dtype=np.float32)
        result = non_max_suppression(boxes, scores)
        assert result.tolist() == [0]

    def test_identical_boxes_keep_only_one(self) -> None:
        boxes = np.array(
            [
                [10.0, 10.0, 50.0, 50.0],
                [10.0, 10.0, 50.0, 50.0],
                [10.0, 10.0, 50.0, 50.0],
            ],
            dtype=np.float32,
        )
        scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
        result = non_max_suppression(boxes, scores)
        assert result.tolist() == [0]

    def test_non_overlapping_boxes_are_all_kept(self) -> None:
        boxes = np.array(
            [
                [0.0, 0.0, 10.0, 10.0],
                [100.0, 100.0, 110.0, 110.0],
                [200.0, 200.0, 210.0, 210.0],
            ],
            dtype=np.float32,
        )
        scores = np.array([0.5, 0.6, 0.7], dtype=np.float32)
        result = non_max_suppression(boxes, scores)
        assert set(result.tolist()) == {0, 1, 2}

    def test_keeps_highest_score_among_overlapping_boxes(self) -> None:
        boxes = np.array(
            [
                [0.0, 0.0, 10.0, 10.0],
                [1.0, 1.0, 11.0, 11.0],
            ],
            dtype=np.float32,
        )
        scores = np.array([0.4, 0.95], dtype=np.float32)
        result = non_max_suppression(boxes, scores, iou_threshold=0.3)
        assert result.tolist() == [1]

    def test_results_are_ordered_by_descending_score(self) -> None:
        boxes = np.array(
            [
                [0.0, 0.0, 10.0, 10.0],
                [500.0, 500.0, 510.0, 510.0],
                [1000.0, 1000.0, 1010.0, 1010.0],
            ],
            dtype=np.float32,
        )
        scores = np.array([0.2, 0.9, 0.5], dtype=np.float32)
        result = non_max_suppression(boxes, scores)
        assert result.tolist() == [1, 2, 0]

    def test_custom_iou_threshold_allows_more_overlap(self) -> None:
        boxes = np.array(
            [
                [0.0, 0.0, 10.0, 10.0],
                [2.0, 2.0, 12.0, 12.0],
            ],
            dtype=np.float32,
        )
        scores = np.array([0.9, 0.8], dtype=np.float32)
        # High threshold: heavy overlap is still tolerated, both kept.
        result = non_max_suppression(boxes, scores, iou_threshold=0.99)
        assert set(result.tolist()) == {0, 1}
