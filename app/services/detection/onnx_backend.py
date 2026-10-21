"""ONNX Runtime CPU backend for YOLOv8 object detection.

Runs anywhere onnxruntime has a CPU build (a Raspberry Pi, an old PC a
customer already owns, etc.) — the reason this backend exists at all is
that minicam ships to customers with wildly different hardware, and CPU
inference is the only common denominator today. A future accelerator (e.g.
Hailo) backend would implement app.services.detection.base.DetectionBackend
instead, without touching the rest of the system.

No opencv, no Pillow: preprocessing (letterbox resize) is pure numpy, same
reasoning as app/services/encoder.py's choice of simplejpeg over Pillow.
"""

from __future__ import annotations

import numpy as np
import onnxruntime as ort

from app.services.detection import coco_classes
from app.services.detection.base import Detection
from app.services.detection.nms import non_max_suppression

MODEL_INPUT_SIZE = 640
LETTERBOX_PAD_VALUE = 114
NUM_BOX_COORDS = 4


class OnnxDetectionBackend:
    """Detects objects in a frame using a YOLOv8 ONNX model, on CPU."""

    def __init__(self, model_path: str, confidence_threshold: float) -> None:
        """Load the ONNX model for CPU inference.

        Args:
            model_path: Absolute path to a YOLOv8 ONNX export (NMS disabled,
                output shape (1, 84, 8400)).
            confidence_threshold: Minimum class score to keep a detection.
        """
        self._session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self._input_name: str = self._session.get_inputs()[0].name
        self._confidence_threshold = confidence_threshold

    @property
    def available_classes(self) -> tuple[str, ...]:
        """Class names this backend can recognize, in COCO order."""
        return coco_classes.COCO_CLASSES

    def detect(
        self, image: np.ndarray, allowed_classes: frozenset[str]
    ) -> list[Detection]:
        """Run YOLOv8 inference and return detections in image-space coords."""
        tensor, scale, pad_x, pad_y = _preprocess(image)
        raw_output = self._session.run(None, {self._input_name: tensor})[0]

        boxes, scores, class_ids = _decode_output(
            raw_output, self._confidence_threshold
        )
        if boxes.shape[0] == 0:
            return []

        keep = non_max_suppression(boxes, scores)
        boxes, scores, class_ids = boxes[keep], scores[keep], class_ids[keep]

        height, width = image.shape[0], image.shape[1]
        boxes = _undo_letterbox(boxes, scale, pad_x, pad_y, width, height)

        return _to_detections(boxes, scores, class_ids, allowed_classes)


def _resize_nearest(image: np.ndarray, new_width: int, new_height: int) -> np.ndarray:
    """Resize an image with nearest-neighbor sampling, pure numpy.

    Nearest-neighbor keeps this dependency-free (no opencv/Pillow) and is
    accurate enough feeding the letterbox step of a detection model.
    """
    height, width = image.shape[0], image.shape[1]
    row_indices = np.clip(
        (np.arange(new_height) * (height / new_height)).astype(np.int64),
        0,
        height - 1,
    )
    col_indices = np.clip(
        (np.arange(new_width) * (width / new_width)).astype(np.int64),
        0,
        width - 1,
    )
    return np.asarray(image[row_indices][:, col_indices])


def _preprocess(image: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    """Letterbox-resize, normalize and reshape a frame for YOLOv8 input.

    Returns:
        The (1, 3, 640, 640) float32 input tensor, the scale factor applied
        and the (pad_x, pad_y) padding offsets — needed later to map
        detections in the 640x640 space back to the original image space.
    """
    height, width = image.shape[0], image.shape[1]
    scale = min(MODEL_INPUT_SIZE / width, MODEL_INPUT_SIZE / height)
    new_width = round(width * scale)
    new_height = round(height * scale)

    resized = _resize_nearest(image, new_width, new_height)

    pad_x = (MODEL_INPUT_SIZE - new_width) / 2
    pad_y = (MODEL_INPUT_SIZE - new_height) / 2
    canvas = np.full(
        (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE, 3),
        LETTERBOX_PAD_VALUE,
        dtype=np.uint8,
    )
    top, left = round(pad_y), round(pad_x)
    canvas[top : top + new_height, left : left + new_width] = resized

    normalized = canvas.astype(np.float32) / 255.0
    chw = normalized.transpose(2, 0, 1)
    tensor = np.expand_dims(chw, axis=0)
    return tensor, scale, pad_x, pad_y


def _cxcywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    """Convert (cx, cy, w, h) boxes to (x1, y1, x2, y2)."""
    cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    x1 = cx - w / 2
    y1 = cy - h / 2
    x2 = cx + w / 2
    y2 = cy + h / 2
    return np.stack([x1, y1, x2, y2], axis=1)


def _decode_output(
    raw_output: np.ndarray, confidence_threshold: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode a raw YOLOv8 (1, 84, 8400) tensor into boxes/scores/class ids.

    Returns:
        boxes: (M, 4) x1,y1,x2,y2 in the 640x640 letterboxed space.
        scores: (M,) confidence of the winning class per box.
        class_ids: (M,) index into COCO_CLASSES per box.
    """
    predictions = raw_output[0].T  # (8400, 84)
    box_coords = predictions[:, :NUM_BOX_COORDS]
    class_scores = predictions[:, NUM_BOX_COORDS:]

    class_ids = np.argmax(class_scores, axis=1)
    scores = np.max(class_scores, axis=1)

    keep_mask = scores >= confidence_threshold
    boxes = _cxcywh_to_xyxy(box_coords[keep_mask])
    return boxes, scores[keep_mask], class_ids[keep_mask]


def _undo_letterbox(
    boxes: np.ndarray,
    scale: float,
    pad_x: float,
    pad_y: float,
    width: int,
    height: int,
) -> np.ndarray:
    """Map boxes from the letterboxed 640x640 space back to image space."""
    boxes = boxes.copy()
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / scale
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width - 1)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height - 1)
    return boxes


def _to_detections(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    allowed_classes: frozenset[str],
) -> list[Detection]:
    """Build Detection objects from decoded arrays, filtered to allowed_classes."""
    detections: list[Detection] = []
    for box, score, class_id in zip(boxes, scores, class_ids, strict=True):
        class_name = coco_classes.COCO_CLASSES[int(class_id)]
        if class_name not in allowed_classes:
            continue
        x1, y1, x2, y2 = box
        detections.append(
            Detection(
                class_name=class_name,
                confidence=float(score),
                x1=int(x1),
                y1=int(y1),
                x2=int(x2),
                y2=int(y2),
            )
        )
    return detections
