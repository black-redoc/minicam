# Object Detection (YOLO)

minicam can run object detection (YOLOv8) on each camera's stream and overlay bounding boxes for the object classes you choose, per camera, from the dashboard. Detection is optional and fully disabled by default — a device with no detection backend configured behaves exactly as it did before this feature existed.

## Why a pluggable backend

minicam ships to customers with very different hardware: a Raspberry Pi 5 alone, a Pi 5 with a Hailo AI accelerator HAT, or an old PC a customer already owns. Rather than hard-coding one inference library, detection is built behind a `DetectionBackend` interface (`app/services/detection/base.py`). Today only one backend exists — CPU inference via `onnxruntime` — because it is the only implementation that runs on any of that hardware without an accelerator. An accelerator-specific backend (e.g. Hailo) is future work and will implement the same interface without touching the rest of the system.

## Enabling detection

Detection is configured in two layers:

1. **Which backend and model run on this device.** This is an installation-wide choice stored in the `[detection]` section of `config.toml`. It is edited from the dashboard's **AI Detection** page, which saves it through `PUT /api/detection/settings` and reloads the backend without a restart. With `backend = "none"` (the default), detection is off for every camera, whatever the per-camera settings say.
2. **Which classes each camera detects.** This is set per camera from the dashboard. Open a camera's config and check the classes you want boxed (e.g. `person`, `dog`). Leaving all classes unchecked disables detection for that camera, even if a backend is active.

The class checkboxes are populated from `GET /api/detection/classes`, which reflects whatever the active backend actually supports — nothing is hard-coded in the dashboard. If no backend is configured, that panel shows "No detection backend configured on this device" instead of an empty list.

## `[detection]` settings

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `"none"` | `"none"` disables detection entirely. `"onnx"` runs the CPU backend. |
| `model_path` | `""` | Absolute path to the `.onnx` model. Required for `"onnx"`. Set automatically when you install a model from the catalog. |
| `confidence_threshold` | `0.5` | Minimum class score to keep a detection. |
| `inference_fps` | `3.0` | How often each camera with active detection runs inference, independent of its stream frame rate. |

```toml
[detection]
backend = "onnx"
model_path = "/home/pi/minicam/app/data/models/yolo11n-int8.onnx"
confidence_threshold = 0.5
inference_fps = 3.0
```

Any misconfiguration (backend set to `"onnx"` but the model file is missing, or fails to load) is logged as a warning and treated the same as `"none"` — detection never blocks the app from starting or a camera's stream from working.

## Models: the minicam-models catalog

The model is not committed to this repository. Models come from the companion repo [`minicam-models`](https://github.com/black-redoc/minicam-models). Its GitHub Actions pipeline exports pinned YOLOv8 and YOLO11 weights to ONNX and checks every file against the contract the ONNX backend expects:

- float32 input `1x3x640x640` and float32 output `1x84x8400`;
- COCO-80 class order;
- no NMS inside the model.

Each GitHub Release then publishes the `.onnx` assets, a `SHA256SUMS` file and a `manifest.json` that lists every asset with its size and sha256.

| Device profile | Recommended asset | Alternatives | Notes |
|---|---|---|---|
| Raspberry Pi 5 (CPU-only) | `yolo11n-int8.onnx` | `yolov8n-int8.onnx`, `yolo11n-fp32.onnx`, `yolov8n-fp32.onnx` | Small network; INT8 is ~2.5x smaller and faster on ARM CPU |
| Old x86 PC (CPU-only) | `yolo11s-fp32.onnx` | `yolov8s-fp32.onnx`, `yolo11s-int8.onnx`, `yolov8s-int8.onnx` | Larger network, more accurate |

INT8 assets keep float32 input and output, so they are drop-in replacements for their FP32 counterparts. A Hailo-accelerated profile isn't published yet, because minicam has no Hailo backend to consume a `.hef` export.

### Installing from the dashboard (recommended)

The **Model catalog** card on the **AI Detection** page lists every asset of the latest minicam-models release, with its size and device profile. Click **Install & activate** next to a model and minicam will:

1. Re-download the release `manifest.json` server-side. The URL and checksum never come from the browser.
2. Download the asset into `app/data/models/<asset>`, computing its sha256 while streaming. A file whose checksum doesn't match the manifest is rejected and never replaces an existing model. If the right file is already on disk, the download is skipped.
3. Set `backend = "onnx"` and `model_path` to the new file, then reload the backend. Confidence threshold and inference FPS are kept.

A model that is on disk but not active shows **Activate** instead. The same flow is available over the API: `GET /api/detection/catalog` and `POST /api/detection/catalog/install` (see [API Reference](/doc/api)).

By default the catalog follows the latest release:

```
https://github.com/black-redoc/minicam-models/releases/latest/download/manifest.json
```

Set `MINICAM_MODEL_CATALOG_URL` (see [Configuration](/doc/configuration)) to pin a specific release, e.g. `https://github.com/black-redoc/minicam-models/releases/download/v0.1.0/manifest.json`, or to use a fork. minicam refuses a manifest whose declared contract (input/output shape, classes, NMS) doesn't match its ONNX backend.

Installing a newer release of an asset with the same name replaces the file on disk. The catalog flow reloads the backend automatically. After a manual **Fetch from URL**, click **Save** to reload it.

### Other ways to install a model

- **Fetch from URL:** paste any asset URL into the **AI Detection** page, which calls `POST /api/detection/model/fetch`. The newest version of an asset is always at `https://github.com/black-redoc/minicam-models/releases/latest/download/<asset>`.
- **Upload model:** upload a `.onnx` file from your computer (`POST /api/detection/model`).

Both return the file's path on disk. Copy it into **Model path**, select the ONNX backend and click **Save** to activate it. Unlike the catalog, these paths have no checksum to compare against.

The URL fetch, including the catalog's, only accepts `https://` URLs whose host resolves to a public address. Redirects (GitHub Release assets redirect to a CDN) are followed manually, up to 5 hops, and every hop is re-checked against the same rules. Downloads and uploads are streamed to a temporary file and moved into place only once complete, so a failed, oversized or tampered transfer never replaces an existing model.

For a custom export that doesn't come from the pipeline, check the file with the same validator the pipeline uses before installing it:

```
uvx --from git+https://github.com/black-redoc/minicam-models minicam-models validate path/to/model.onnx
```

## How it works

- One background task per camera with active detection classes (`app/services/detection/worker.py`) reads the camera's frame buffer at `MINICAM_DETECTION_INFERENCE_FPS` and runs inference **once per camera**, regardless of how many browser tabs are watching that camera's stream — inference is far more expensive than the JPEG encode documented in [Video Streaming](/doc/stream), so it must not scale with viewer count.
- Bounding boxes are drawn on each frame right before its single shared JPEG encode (once per camera, not per viewer), using plain numpy (no Pillow/OpenCV — see the note in `app/services/encoder.py` about why Pillow was already ruled out on this exact hot path). Each detected class gets a deterministic border color; there are no text labels in this version.
- Each detection result expires after 3 expected result periods (inference interval + last inference time, minimum 1 s). If detection stalls or keeps failing, viewers get the plain frame instead of boxes that no longer match the picture.
- Detection classes are validated server-side: `POST`/`PUT /api/cameras` rejects any class name the active backend doesn't recognize with a `400`.
- Creating, editing, renaming, or deleting a camera starts/stops its detection worker the same way it already starts/stops the camera's RTSP capture and PTZ service — see `app/routers/setup.py`.

## Roadmap

Push notifications on detection and per-camera alarm schedules (e.g. only alert outside business hours) are planned as later, separate additions on top of this detection engine.
