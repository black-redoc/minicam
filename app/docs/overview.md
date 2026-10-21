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
  ├─ FrameBuffer        — latest-frame slot, wakes async readers on write
  ├─ SharedJpegEncoder  — encodes each frame once per camera for all viewers
  ├─ AuthorizationService / WanAccessGateService — per-camera + WAN access checks
  ├─ TokenService / StreamTokenService — session JWT and short-lived stream JWT
  └─ app.state          — global dict of cameras, buffers, ptz services
```

Every request that touches a camera (viewing it on the dashboard, opening its WebSocket, or sending a PTZ command) requires a logged-in session, and most also require an explicit per-camera permission. See [Authentication & Authorization](/doc/auth) for the full model.

## Main Components

### RTSPCamera (`app/services/camera.py`)
Reads an RTSP stream using `PyAV`, decodes frames to raw RGB24 numpy arrays (`app/services/decoder.py`), and writes them into a `FrameBuffer`. Runs on a background daemon thread named `rtsp:<camera name>`. When the stream drops it reconnects with exponential backoff (2 s, 4 s, 8 s … capped at 60 s, ±20 % jitter), and the delay resets once a connection delivers a frame. JPEG encoding is *not* done here — it happens on demand, once per frame per camera, shared by all of that camera's viewers (see [Video Streaming](/doc/stream)).

### PTZService (`app/services/ptz.py`)
Wraps the `onvif-zeep` library. Connects lazily on first PTZ command. Sends `ContinuousMove` SOAP requests (speed 0.5). Supports `up`, `down`, `left`, `right`, and `stop`.

### FrameBuffer (`app/buffers/frame_buffer.py`)
Holds only the latest frame. The capture thread replaces it with a single attribute assignment (atomic under the GIL, so no lock or hand-off thread), and `read()` is a lock-free snapshot. WebSocket handlers instead `await next_frame()`, which sleeps until a write wakes it through `loop.call_soon_threadsafe` — no polling.

### SharedJpegEncoder (`app/services/shared_encoder.py`)
One per camera buffer. The first viewer to need a frame starts its encode in the default executor; every other viewer of that camera awaits the same result. Encoding CPU scales with the camera's frame rate, not with the number of viewers.

### Security layer (`app/services/auth.py`, `authorization.py`, `wan_access.py`, `stream_tokens.py`)
Backed by a SQLite database (`app/db/migrations/0001_initial.sql`) with `users`, `sessions`, `camera_permissions`, and `wan_access` tables. Schema changes are numbered SQL files in `app/db/migrations/` (`NNNN_<name>.sql`, one statement per line ending in `;`). On startup (and from the `create_user` CLI) each file that is not yet recorded in the `schema_migrations` table runs once, in its own transaction, so a new migration can use plain `ALTER TABLE` and never runs twice. See [Authentication & Authorization](/doc/auth) for details on sessions, per-camera permissions, and the WAN access gate.

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
