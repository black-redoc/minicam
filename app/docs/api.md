# API Reference

Complete reference for all HTTP and WebSocket endpoints exposed by minicam. Unless noted otherwise, every `/api/*` endpoint below requires a valid session cookie — see [Authentication & Authorization](/doc/auth) for the session/permission/WAN-gate model, response codes (`401`/`403`/`404`), and the `minicam_session` cookie itself.

## Endpoint Summary

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | session (redirects to `/login` if none) | Dashboard HTML, filtered to viewable cameras |
| `GET` | `/login` | none | Login page |
| `GET` | `/admin` | admin | Admin page (user + permission management) |
| `GET` | `/health` | none | App health status |
| `POST` | `/api/auth/login` | none | Log in, set session cookie |
| `POST` | `/api/auth/logout` | session (optional) | Revoke session, clear cookie |
| `GET` | `/api/auth/me` | session | Current user |
| `POST` | `/api/auth/users` | admin | Create a user |
| `GET` | `/api/auth/users` | admin | List all users |
| `DELETE` | `/api/auth/users/{user_id}` | admin | Delete a user |
| `POST` | `/api/auth/permissions` | admin | Grant/update a user's camera permission |
| `GET` | `/api/auth/permissions?user_id=` | admin | List a user's camera permissions |
| `POST` | `/api/stream/{camera_name}/token` | session + `can_view` | Issue a short-lived WebSocket stream token |
| `WS` | `/ws/stream/{camera_name}?token=` | stream token | Binary JPEG stream |
| `POST` | `/api/ptz/{camera_name}/{action}` | session + `can_use_ptz` | PTZ move/stop |
| `GET` | `/api/cameras` | admin | List all cameras |
| `GET` | `/api/cameras/{name}` | admin | Get single camera |
| `POST` | `/api/cameras` | admin | Add camera |
| `PUT` | `/api/cameras/{name}` | admin | Edit camera |
| `DELETE` | `/api/cameras/{name}` | admin | Remove camera |
| `GET` | `/api/setup/discover` | admin | Discover cameras on LAN |
| `GET` | `/doc/` | none | Documentation index |
| `GET` | `/doc/{topic}` | none | Documentation topic |

See [Authentication & Authorization](/doc/auth) for the full `/api/auth/*` reference (request/response bodies, error codes). This page focuses on the camera-facing endpoints.

---

## `GET /health`

Returns current application status.

**Response `200`:**
```json
{
  "status": "ok",
  "cameras": 2,
  "debug": false
}
```

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
