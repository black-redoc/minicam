# API Reference

Complete reference for all HTTP and WebSocket endpoints exposed by minicam.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/health` | App health status |
| `WS` | `/ws/stream/{camera_name}` | Binary JPEG stream |
| `POST` | `/api/ptz/{camera_name}/{action}` | PTZ move/stop |
| `GET` | `/api/cameras` | List all cameras |
| `GET` | `/api/cameras/{name}` | Get single camera |
| `POST` | `/api/cameras` | Add camera |
| `PUT` | `/api/cameras/{name}` | Edit camera |
| `DELETE` | `/api/cameras/{name}` | Remove camera |
| `GET` | `/api/setup/discover` | Discover cameras on LAN |
| `GET` | `/doc/` | Documentation index |
| `GET` | `/doc/{topic}` | Documentation topic |

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

Add a new camera. Restarts camera services.

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

**Response `201`:**
```json
{ "ok": true, "name": "patio" }
```

**Response `409`** (name already exists):
```json
{ "detail": "Camera 'patio' already exists" }
```

---

## `PUT /api/cameras/{name}`

Edit an existing camera. Stops, updates, and restarts the camera service.

**Request body:** Same schema as POST.

**Response `200`:**
```json
{ "ok": true, "name": "patio" }
```

---

## `DELETE /api/cameras/{name}`

Remove a camera. Stops its thread and removes it from config.

**Response `200`:**
```json
{ "ok": true }
```

---

## `POST /api/ptz/{camera_name}/{action}`

Send a PTZ command. Valid actions: `up`, `down`, `left`, `right`, `stop`.

**Response `200`:**
```json
{ "ok": true }
```

---

## `GET /api/setup/discover`

Scan the LAN for ONVIF cameras using WS-Discovery.

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
