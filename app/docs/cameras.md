# Multi-Camera Setup

minicam supports any number of simultaneous cameras. Each camera runs its own RTSP decoder thread and PTZ service instance.

## Adding a Camera via the UI

1. Open the dashboard at `http://<host>:<port>/`.
2. Click the **Settings** (gear) icon in the top-right area.
3. In the configuration modal, fill in all camera fields.
4. Click **Save**. The server restarts the camera service automatically.

## Adding a Camera via the API

Send a `POST` to `/api/cameras` with a JSON body:

```json
{
  "name": "garage",
  "host": "192.168.1.105",
  "rtsp_port": 554,
  "onvif_port": 80,
  "user": "admin",
  "password": "secret",
  "channel": 1,
  "stream": "main"
}
```

**Response `201`:** The created camera (password redacted).
```json
{
  "name": "garage",
  "host": "192.168.1.105",
  "rtsp_port": 554,
  "onvif_port": 80,
  "user": "admin",
  "password": "",
  "channel": 1,
  "stream": "main"
}
```

## Editing a Camera

Send a `PUT` to `/api/cameras/{name}` with the updated fields (same body schema as POST). Leave `password` empty (`""`) to keep the camera's existing password.

**Response `200`:** Same schema as the creation response above.

After a successful edit:
1. The old `RTSPCamera` thread is stopped.
2. Its `DoubleFrameBuffer` is shut down.
3. New instances are created and started with the updated config.
4. `config.toml` is rewritten on disk.

## Deleting a Camera

Send a `DELETE` to `/api/cameras/{name}`.

**Response `200`:**
```json
{ "status": "ok" }
```

The camera thread is stopped, its buffer is released, and the entry is removed from `config.toml`.

## Listing Cameras

`GET /api/cameras` returns the full list:

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

> **Security note:** Passwords are never included in GET responses.

## Camera Name Rules

- Must be unique across all cameras.
- Used as a URL path segment — avoid spaces and special characters.
- Case-sensitive (`Entrada` ≠ `entrada`).
