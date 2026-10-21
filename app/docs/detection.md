# Object Detection (YOLO)

minicam can run object detection (YOLOv8) on each camera's stream and overlay bounding boxes for the object classes you choose, per camera, from the dashboard. Detection is optional and fully disabled by default — a device with no detection backend configured behaves exactly as it did before this feature existed.

## Why a pluggable backend

minicam ships to customers with very different hardware: a Raspberry Pi 5 alone, a Pi 5 with a Hailo AI accelerator HAT, or an old PC a customer already owns. Rather than hard-coding one inference library, detection is built behind a `DetectionBackend` interface (`app/services/detection/base.py`). Today only one backend exists — CPU inference via `onnxruntime` — because it is the only implementation that runs on any of that hardware without an accelerator. An accelerator-specific backend (e.g. Hailo) is future work and will implement the same interface without touching the rest of the system.

## Enabling detection

Detection is configured in two layers:

1. **Which backend runs on this device** — an installation-wide choice, set once via environment variables (see below). If unset, detection is off for every camera regardless of per-camera settings.
2. **Which classes each camera detects** — a per-camera, dashboard-managed setting. Open a camera's config in the dashboard and check the classes you want boxed (e.g. `person`, `dog`). Leaving all classes unchecked disables detection for that camera, even if a backend is active.

The class checkboxes are populated from `GET /api/detection/classes`, which reflects whatever the active backend actually supports — nothing is hard-coded in the dashboard. If no backend is configured, that panel shows "No detection backend configured on this device" instead of an empty list.

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `MINICAM_DETECTION_BACKEND` | no | `"none"` | `"none"` disables detection entirely. `"onnx"` runs the CPU backend below. |
| `MINICAM_DETECTION_MODEL_PATH` | required for `"onnx"` | — | Absolute path to a YOLOv8 ONNX export (NMS disabled, output shape `(1, 84, 8400)`). |
| `MINICAM_DETECTION_CONFIDENCE_THRESHOLD` | no | `0.5` | Minimum class score to keep a detection. |
| `MINICAM_DETECTION_INFERENCE_FPS` | no | `3` | How often each camera with active detection runs inference, independent of its stream frame rate. |

Any misconfiguration (backend set to `"onnx"` but the model file is missing, or fails to load) is logged as a warning and treated the same as `"none"` — detection never blocks the app from starting or a camera's stream from working.

## Getting a model file

The model is not committed to this repository. The recommended source is the `minicam_weights` companion repo, which runs a GitHub Actions pipeline that exports pinned YOLOv8 models to ONNX and publishes them as GitHub Release assets — each release includes a sha256 checksum per asset and a `manifest.json` recording the exact `ultralytics` version used.

| Device profile | Asset | Notes |
|---|---|---|
| Raspberry Pi 5 (CPU-only) | `yolov8n-cpu.onnx` | Smaller network, faster on ARM CPU |
| Old x86 PC (CPU-only) | `yolov8s-cpu.onnx` | Larger network, more accurate |

Both assets share the same input/output contract described above, so the ONNX backend needs no changes between them — only `MINICAM_DETECTION_MODEL_PATH` changes. A Hailo-accelerated profile isn't published yet: the Hailo backend doesn't exist in minicam yet (only driver-presence detection), so there's nothing to consume a `.hef` export.

To install a release asset, either:
- Paste the asset's GitHub Release URL into the dashboard's AI Detection page ("Fetch from URL"), which calls `POST /api/detection/model/fetch` to download it server-side, or
- Upload a `.onnx` file manually via the same page ("Upload model" / `POST /api/detection/model`).

The URL fetch only accepts `https://` URLs whose host resolves to a public address. Redirects (GitHub Release assets redirect to a CDN) are followed manually, up to 5 hops, and every hop is re-checked against the same rules. Downloads and uploads are streamed to a temporary file and moved into place only once complete, so a failed or oversized transfer never replaces an existing model.

Either way, the endpoint returns the file's path on disk — copy it into `model_path` and save to activate it (or set `MINICAM_DETECTION_MODEL_PATH` directly and skip the dashboard).

For a one-off or custom export (a different model size, a different `ultralytics` version) that doesn't go through the pipeline, the same command it runs can be used directly:

```
uv run --with ultralytics==8.4.95 yolo export model=yolov8n.pt format=onnx
```

Record the exact `ultralytics` version and the resulting file's checksum wherever you track deployment artifacts for that device, then copy the `.onnx` file to the path `MINICAM_DETECTION_MODEL_PATH` points to.

## How it works

- One background task per camera with active detection classes (`app/services/detection/worker.py`) reads the camera's frame buffer at `MINICAM_DETECTION_INFERENCE_FPS` and runs inference **once per camera**, regardless of how many browser tabs are watching that camera's stream — inference is far more expensive than the JPEG encode documented in [Video Streaming](/doc/stream), so it must not scale with viewer count.
- Bounding boxes are drawn on each frame right before its single shared JPEG encode (once per camera, not per viewer), using plain numpy (no Pillow/OpenCV — see the note in `app/services/encoder.py` about why Pillow was already ruled out on this exact hot path). Each detected class gets a deterministic border color; there are no text labels in this version.
- Each detection result expires after 3 expected result periods (inference interval + last inference time, minimum 1 s). If detection stalls or keeps failing, viewers get the plain frame instead of boxes that no longer match the picture.
- Detection classes are validated server-side: `POST`/`PUT /api/cameras` rejects any class name the active backend doesn't recognize with a `400`.
- Creating, editing, renaming, or deleting a camera starts/stops its detection worker the same way it already starts/stops the camera's RTSP capture and PTZ service — see `app/routers/setup.py`.

## Roadmap

Push notifications on detection and per-camera alarm schedules (e.g. only alert outside business hours) are planned as later, separate additions on top of this detection engine.
