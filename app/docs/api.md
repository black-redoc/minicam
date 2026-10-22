# API Reference

Complete reference for all HTTP and WebSocket endpoints exposed by minicam. Unless noted otherwise, every `/api/*` endpoint below requires a valid session cookie — see [Authentication & Authorization](/doc/auth) for the session/permission/WAN-gate model, response codes (`401`/`403`/`404`), and the `minicam_session` cookie itself.

## Endpoint Summary

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | session (redirects to `/login` if none) | Dashboard HTML, filtered to viewable cameras |
| `GET` | `/login` | none | Login page |
| `GET` | `/admin` | admin | Admin page (user + permission management) |
| `GET` | `/health` | none | Health summary (counts only) |
| `GET` | `/api/health/cameras` | admin | Per-camera health status |
| `GET` | `/api/detection/catalog` | admin | minicam-models catalog with local install state |
| `POST` | `/api/detection/catalog/install` | admin | Install (and activate) a catalog model by name |
| `POST` | `/api/auth/login` | none | Log in, set session cookie |
| `GET` | `/api/auth/setup` | none | Whether first-run setup is pending |
| `POST` | `/api/auth/setup` | none (LAN, only while no user exists) | Create the first admin and sign in |
| `POST` | `/api/auth/logout` | session (optional) | Revoke session, clear cookie |
| `GET` | `/api/auth/me` | session | Current user |
| `POST` | `/api/auth/users` | admin | Create a user |
| `GET` | `/api/auth/users` | admin | List all users |
| `DELETE` | `/api/auth/users/{user_id}` | admin | Delete a user |
| `POST` | `/api/auth/permissions` | admin | Grant/update a user's camera permission |
| `GET` | `/api/auth/wan-access?user_id=` | admin | Whether a user has WAN access |
| `POST` | `/api/auth/wan-access` | admin | Grant/revoke a user's WAN access |
| `GET` | `/api/auth/permissions?user_id=` | admin | List a user's camera permissions |
| `GET` | `/api/me/cameras` | session | Cameras the caller can view, as JSON (mobile apps) |
| `GET` | `/api/mobile/access-handoff?state=` | WAN + Cloudflare Access | Hand the Access token to the mobile app (`302` to `minicam://`) |
| `POST` | `/api/stream/{camera_name}/token` | session + `can_view` | Issue a short-lived WebSocket stream token |
| `WS` | `/ws/stream/{camera_name}?token=` | stream token | Binary JPEG stream |
| `POST` | `/api/ptz/{camera_name}/{action}` | session + `can_use_ptz` | PTZ move/stop |
| `GET` | `/api/cameras` | admin | List all cameras |
| `GET` | `/api/cameras/{name}` | admin | Get single camera |
| `POST` | `/api/cameras` | admin | Add camera |
| `PUT` | `/api/cameras/{name}` | admin | Edit camera |
| `DELETE` | `/api/cameras/{name}` | admin | Remove camera |
| `GET` | `/api/setup/discover` | admin | Discover cameras on LAN |
| `GET` | `/api/notifications/settings` | admin | Global detection notification settings |
| `PUT` | `/api/notifications/settings` | admin | Save global detection notification settings |
| `GET` | `/api/notifications/events` | session + WAN gate | SSE feed of detections on viewable cameras |
| `GET` | `/api/notifications/push/public-key` | session | VAPID public key for Web Push |
| `POST` | `/api/notifications/push/subscriptions` | session + WAN gate | Register a Web Push subscription |
| `DELETE` | `/api/notifications/push/subscriptions` | session | Unregister a Web Push subscription |
| `GET` | `/service-worker.js` | none | Service worker that displays Web Push notifications |
| `GET` | `/recordings` | session (redirects to `/login` if none) | Event recordings page |
| `GET` | `/api/recordings/settings` | admin | Recording settings, disk usage and selectable storage limits |
| `PUT` | `/api/recordings/settings` | admin | Save recording settings |
| `GET` | `/api/recordings` | session + WAN gate | Clips of viewable cameras, newest first |
| `GET` | `/api/recordings/{id}/video` | session + `can_view` + WAN gate | A clip's MP4 file |
| `DELETE` | `/api/recordings/{id}` | admin | Delete a clip |
| `GET` | `/doc/` | none | Documentation index |
| `GET` | `/doc/{topic}` | none | Documentation topic |

See [Authentication & Authorization](/doc/auth) for the full `/api/auth/*` reference (request/response bodies, error codes). This page focuses on the camera-facing endpoints. The notification endpoints are described in [Detection Notifications](/doc/notifications), and the recording endpoints in [Event Recordings](/doc/recordings).

---

## `GET /health`

Public, unauthenticated summary for uptime monitors. It returns counts only: camera names and config details stay behind authorization. It always answers `200`. A camera being down shows up as `"degraded"`, which is not a reason for a supervisor to restart the app.

**Response `200`:**
```json
{
  "status": "degraded",
  "cameras": 2,
  "cameras_connected": 1
}
```

## `GET /api/health/cameras`

Per-camera runtime status, sorted by name. Requires an **administrator session**.

**Response `200`:**
```json
[
  {"name": "garage", "connected": true, "last_frame_age_seconds": 0.04, "detection_active": true},
  {"name": "porch", "connected": false, "last_frame_age_seconds": 812.3, "detection_active": false}
]
```

`last_frame_age_seconds` is the time since the capture thread last delivered a frame (`null` if it never has). A large value on a camera reported as `connected` means the stream is stalled.

---

## `GET /api/detection/catalog`

Lists the assets of the configured minicam-models release (`MINICAM_MODEL_CATALOG_URL`). Requires an **administrator session**. `installed` means a file with the expected size is in `app/data/models/`. `active` means it is the configured ONNX model.

**Response `200`:**
```json
{
  "catalog_url": "https://github.com/black-redoc/minicam-models/releases/latest/download/manifest.json",
  "release": "v0.1.0",
  "assets": [
    {"name": "yolo11n-int8.onnx", "model": "yolo11n", "family": "YOLO11", "precision": "int8",
     "profile": "Raspberry Pi 5 (CPU)", "bytes": 4590317, "sha256": "1ca298c7…", "url": "https://…",
     "installed": true, "active": true}
  ]
}
```

**Response `502`:** the manifest could not be fetched, or it is invalid or incompatible with this minicam.

## `POST /api/detection/catalog/install`

Installs a catalog asset by name, verifying its sha256 against a fresh copy of the manifest, and by default activates it as the ONNX model. Requires an **administrator session**.

**Request body:** `{"name": "yolo11n-int8.onnx", "activate": true}`. `activate` is optional and defaults to `true`.

**Response `200`:** `{"model_path": "/…/app/data/models/yolo11n-int8.onnx", "sha256": "1ca298c7…", "active": true}`

**Response `404`:** no asset with that name in the catalog. **Response `502`:** fetch failure or checksum mismatch.

---

## Camera Endpoints

> Every endpoint in this section (`/api/cameras*`, `/api/setup/discover`) requires an **administrator session** — `401` with no session cookie, `403` for a logged-in non-admin user. See [Authentication & Authorization](/doc/auth).

## `GET /api/cameras`

Returns list of all configured cameras. Passwords are omitted.

**Response `200`:**
```json
[
  {
    "name": "entrada",
    "host": "192.168.1.100",
    "rtsp_port": 554,
    "onvif_port": 80,
    "user": "admin",
    "password": "",
    "channel": 1,
    "stream": "main"
  }
]
```

---

## `GET /api/cameras/{name}`

Get a single camera by name.

**Response `200`:** Same schema as list item above.

**Response `404`:**
```json
{ "detail": "Camera 'xxx' not found" }
```

---

## `POST /api/cameras`

Add a new camera and start its RTSP capture / PTZ services.

**Request body:**
```json
{
  "name": "patio",
  "host": "192.168.1.101",
  "rtsp_port": 554,
  "onvif_port": 80,
  "user": "admin",
  "password": "secret",
  "channel": 1,
  "stream": "main"
}
```

`name` and `host` are required (non-empty). `rtsp_port` and `onvif_port` must be in `1`–`65535`, `channel` must be `>= 1`. Any field left out falls back to its default (see [Configuration](/doc/configuration)). Violating a constraint returns `422`.

**Response `201`:** The created camera, same schema as a `GET /api/cameras/{name}` item (password redacted).
```json
{
  "name": "patio",
  "host": "192.168.1.101",
  "rtsp_port": 554,
  "onvif_port": 80,
  "user": "admin",
  "password": "",
  "channel": 1,
  "stream": "main"
}
```

**Response `409`** (name already exists):
```json
{ "detail": "Camera 'patio' already exists" }
```

---

## `PUT /api/cameras/{name}`

Edit an existing camera. Stops, updates, and restarts the camera service.

**Request body:** Same schema as POST. Leave `password` empty (`""`) to keep the camera's existing password — any non-empty value replaces it.

**Response `200`:** Same schema as the POST response above.

**Response `404`** (camera not found):
```json
{ "detail": "Camera 'patio' not found" }
```

---

## `DELETE /api/cameras/{name}`

Remove a camera. Stops its thread and removes it from config.

**Response `200`:**
```json
{ "status": "ok" }
```

**Response `404`** (camera not found):
```json
{ "detail": "Camera 'patio' not found" }
```

---

## `GET /api/me/cameras`

Requires a session (any role). Lists the cameras the caller has `can_view` on, in config order — the same filter as the dashboard. Used by the native mobile apps, which can't read the server-rendered dashboard.

**Response `200`:**
```json
[{ "name": "entrada", "can_use_ptz": true }]
```

**Response `401`** (no session).

---

## `GET /api/mobile/access-handoff?state=<state>`

Used by the mobile apps to get a Cloudflare Access token. See [Cloudflare Access & Tunnel → Mobile apps](/doc/cloudflare#mobile-apps).

**Response `302`** to `minicam://access-callback?state=<state>#token=<Access JWT>` (`Cache-Control: no-store`). **`400`** if `state` isn't 32-128 characters of `[A-Za-z0-9_-]`. **`403`** on LAN, if Access isn't configured, or if the `Cf-Access-Jwt-Assertion` header is missing or invalid.

---

## `POST /api/stream/{camera_name}/token`

Requires a session with `can_view` on `camera_name` (and, off-LAN, an active WAN access grant). Issues a short-lived token that authorizes exactly one `WS /ws/stream/{camera_name}` handshake.

**Response `200`:**
```json
{ "token": "<jwt>", "expires_in": 30 }
```

**Response `401`** (no session) / **`403`** (WAN access gate closed) / **`404`** (no `can_view` permission on this camera). See [Video Streaming](/doc/stream) for the full handshake and the `WS /ws/stream/{camera_name}?token=` endpoint.

---

## `POST /api/ptz/{camera_name}/{action}`

Send a PTZ command. Valid actions: `up`, `down`, `left`, `right`, `stop`. Requires a session with `can_use_ptz` on `camera_name` (and, off-LAN, an active WAN access grant).

**Response `200`:**
```json
{ "action": "up", "status": "ok" }
```

**Response `400`** (unknown action) / **`401`** (no session) / **`403`** (WAN access gate closed) / **`404`** (no `can_use_ptz` permission on this camera) / **`503`** (no PTZ service registered, or ONVIF unreachable after an automatic reconnect attempt) — see [PTZ Control](/doc/ptz) for details.

---

## `GET /api/setup/discover`

Scan the LAN for ONVIF cameras using WS-Discovery. Requires an administrator session.

**Response `200`:**
```json
[
  {
    "host": "192.168.1.105",
    "name": "IPCamera",
    "hardware": "IPCamera",
    "location": "192.168.1.105"
  }
]
```

See [Camera Discovery](/doc/discovery) for full details.
