# Overview

minicam is a self-hosted IP camera dashboard built with **FastAPI**. It streams live video from RTSP cameras, provides real-time PTZ (Pan-Tilt-Zoom) control via the ONVIF protocol, and gates every camera behind a session-authenticated, per-camera permission model.

## Architecture

```
HTTP client (browser)
        │
        ▼
  FastAPI (main.py)
        │
  ┌─────┴───────────────────────────────┐
  │                                     │
  ▼                                     ▼
Routers                             Static files
  ├─ auth (POST /api/auth/…)            app/static/
  ├─ dashboard (GET /, /login, /admin)
  ├─ stream (POST /api/stream/…/token, WS /ws/stream/…)
  ├─ ptz (POST /api/ptz/…)
  ├─ setup (GET|POST /api/cameras/…)
  └─ docs (GET /doc/…)
        │
        ▼
   Services / State
  ├─ RTSPCamera         — decodes RTSP stream into raw RGB24 frames
  ├─ PTZService         — sends ONVIF ContinuousMove commands
  ├─ DoubleFrameBuffer  — single-producer double buffer for frame sharing
  ├─ AuthorizationService / WanAccessGateService — per-camera + WAN access checks
  ├─ TokenService / StreamTokenService — session JWT and short-lived stream JWT
  └─ app.state          — global dict of cameras, buffers, ptz services
```

Every request that touches a camera (viewing it on the dashboard, opening its WebSocket, or sending a PTZ command) requires a logged-in session, and most also require an explicit per-camera permission. See [Authentication & Authorization](/doc/auth) for the full model.

## Main Components

### RTSPCamera (`app/services/camera.py`)
Reads an RTSP stream using `PyAV`, decodes frames to raw RGB24 numpy arrays (`app/services/decoder.py`), and writes them into a `DoubleFrameBuffer`. Runs on a background daemon thread. JPEG encoding is *not* done here — it happens on demand per WebSocket connection (see [Video Streaming](/doc/stream)).

### PTZService (`app/services/ptz.py`)
Wraps the `onvif-zeep` library. Connects lazily on first PTZ command. Sends `ContinuousMove` SOAP requests (speed 0.5). Supports `up`, `down`, `left`, `right`, and `stop`.

### DoubleFrameBuffer (`app/buffers/double_frame_buffer.py`)
Single-producer double buffer: writes are submitted to a dedicated single-worker `ThreadPoolExecutor`, which serializes the `_read`/`_write` slot swap without an explicit lock. Reads are lock-free — CPython's GIL guarantees that loading the `_read` attribute is atomic.

### Security layer (`app/services/auth.py`, `authorization.py`, `wan_access.py`, `stream_tokens.py`)
Backed by a SQLite database (`app/db/migrations/0001_initial.sql`) with `users`, `sessions`, `camera_permissions`, and `wan_access` tables. See [Authentication & Authorization](/doc/auth) for details on sessions, per-camera permissions, and the WAN access gate.

### Routers
| Router | Prefix | Responsibility |
|--------|--------|----------------|
| auth | `/api/auth` | Login/logout/session, user + permission management |
| dashboard | `/`, `/login`, `/admin` | Renders the Jinja2 HTML dashboard, login page, and admin page |
| stream | `/api/stream`, `/ws/stream` | Stream token issuance + WebSocket JPEG stream |
| ptz | `/api/ptz` | PTZ move/stop commands |
| setup | `/api/cameras`, `/api/setup` | Camera CRUD + discovery (admin-only) |
| docs | `/doc` | This documentation system |

## Configuration
Camera list and app settings live in `config.toml`. See [Configuration](/doc/configuration) for details. Security-related settings (JWT secret, session/stream token TTLs, database path) are read from environment variables — see [Configuration](/doc/configuration#security-settings).
