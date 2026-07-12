# Overview

minicam is a self-hosted IP camera dashboard built with **FastAPI**. It streams live video from RTSP cameras and provides real-time PTZ (Pan-Tilt-Zoom) control via the ONVIF protocol.

## Architecture

```
HTTP client (browser)
        │
        ▼
  FastAPI (main.py)
        │
  ┌─────┴──────────────────────┐
  │                            │
  ▼                            ▼
Routers                    Static files
  ├─ dashboard (GET /)          app/static/
  ├─ stream (WS /ws/stream/…)
  ├─ ptz (POST /api/ptz/…)
  ├─ setup (GET|POST /api/cameras/…)
  └─ docs (GET /doc/…)
        │
        ▼
   Services / State
  ├─ RTSPCamera         — decodes RTSP stream into raw RGB24 frames
  ├─ PTZService         — sends ONVIF ContinuousMove commands
  ├─ DoubleFrameBuffer  — single-producer double buffer for frame sharing
  └─ app.state          — global dict of cameras, buffers, ptz services
```

## Main Components

### RTSPCamera (`app/services/camera.py`)
Reads an RTSP stream using `PyAV`, decodes frames to raw RGB24 numpy arrays (`app/services/decoder.py`), and writes them into a `DoubleFrameBuffer`. Runs on a background daemon thread. JPEG encoding is *not* done here — it happens on demand per WebSocket connection (see [Video Streaming](/doc/stream)).

### PTZService (`app/services/ptz.py`)
Wraps the `onvif-zeep` library. Connects lazily on first PTZ command. Sends `ContinuousMove` SOAP requests (speed 0.5). Supports `up`, `down`, `left`, `right`, and `stop`.

### DoubleFrameBuffer (`app/buffers/double_frame_buffer.py`)
Single-producer double buffer: writes are submitted to a dedicated single-worker `ThreadPoolExecutor`, which serializes the `_read`/`_write` slot swap without an explicit lock. Reads are lock-free — CPython's GIL guarantees that loading the `_read` attribute is atomic.

### Routers
| Router | Prefix | Responsibility |
|--------|--------|----------------|
| dashboard | `/` | Renders the Jinja2 HTML dashboard |
| stream | `/ws/stream` | WebSocket JPEG stream |
| ptz | `/api/ptz` | PTZ move/stop commands |
| setup | `/api/cameras`, `/api/setup` | Camera CRUD + discovery |
| docs | `/doc` | This documentation system |

## Configuration
Camera list and app settings live in `config.toml`. See [Configuration](/doc/configuration) for details.
