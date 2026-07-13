# Multi-Camera Setup

minicam supports any number of simultaneous cameras. Each camera runs its own RTSP decoder thread and PTZ service instance.

## Authorization

Every `/api/cameras` and `/api/setup/discover` endpoint on this page requires an **administrator session** (see [Authentication & Authorization](/doc/auth)) — `401` with no session, `403` for a logged-in non-admin user. Regular users never see other users' cameras: what they can view/control on the dashboard is governed separately by per-camera `can_view`/`can_use_ptz` permissions, granted by an admin from the `/admin` page.

## Adding a Camera via the UI

1. Log in as an administrator and open the dashboard at `http://<host>:<port>/`.
2. Click the **Settings** (gear) icon in the top-right area, then **Camera settings**.
3. In the configuration modal, fill in all camera fields (or use **Discover cameras** — see [Camera Discovery](/doc/discovery)).
4. Click **Save**. The server restarts the camera service automatically.

> The "Camera settings" menu item is currently shown to every logged-in user, not just admins — but the underlying API calls will fail with `401`/`403` for a non-admin. Only admins can actually add, edit, or remove cameras.

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

## Viewing Cameras on the Dashboard

The dashboard has three view modes, cycled with the navbar view button in a bounce pattern (`single → grid → stack → grid → single → …`, `app/static/js/view-cycle.js`):

| Mode | Layout | Notes |
|------|--------|-------|
| `single` | One camera, full width | Navbar shows that camera's live FPS |
| `grid` | Responsive multi-column grid (1–3 columns depending on camera count) | Each cell shows its own FPS badge; navbar FPS is hidden |
| `stack` | Same grid renderer forced to a single column | All cameras stacked vertically; per-cell FPS badges as in grid mode |

Clicking a cell in grid or stack view switches to `single` view on that camera (`grid:select` event).
