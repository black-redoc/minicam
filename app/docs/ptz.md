# PTZ Control

minicam controls Pan-Tilt-Zoom via the **ONVIF** protocol (SOAP over HTTP). PTZ is available for any camera whose `onvif_port` is configured.

## Endpoint

```
POST /api/ptz/{camera_name}/{action}
```

## Authorization

The endpoint requires a valid session and the `can_use_ptz` permission on `camera_name` (see [Authentication & Authorization](/doc/auth)). Off-LAN callers additionally need an active WAN access grant.

- **`401`** — no valid session.
- **`404`** — the caller lacks `can_use_ptz` on this camera (same status as "camera doesn't exist," so an unauthorized caller can't tell the two apart).
- **`403`** — request arrived from outside the LAN and the caller's WAN access gate is not active.

## Actions

| Action | Description |
|--------|-------------|
| `up` | Pan/tilt upward (continuous) |
| `down` | Pan/tilt downward (continuous) |
| `left` | Pan left (continuous) |
| `right` | Pan right (continuous) |
| `stop` | Stop all movement |

## Response

All actions return:

```json
{ "action": "up", "status": "ok" }
```

**Response `400`** (unknown action):

```json
{ "detail": "Unknown PTZ action: 'xyz'" }
```

**Response `503`** (no PTZ service registered for the camera, or ONVIF unreachable even after an automatic reconnect attempt):

```json
{ "detail": "PTZ service not found for camera 'entrada'" }
```

## Speed

Movement speed is fixed at **0.5** in both X (pan) and Y (tilt) axes. This maps directly to the ONVIF `ContinuousMove` `Velocity` parameter (range 0.0–1.0).

To change the speed, edit `PTZService` in `app/services/ptz.py`.

## Connection Lifecycle

`PTZService` connects **lazily** — the first PTZ command triggers the ONVIF handshake. Subsequent commands reuse the same connection.

Blocking ONVIF/SOAP calls run on a dedicated 4-worker `ThreadPoolExecutor` (`state.ptz_executor`, `app/state.py`), kept separate from the default executor so a burst of PTZ activity never delays JPEG frame encoding for any viewer.

If a command fails, the `/api/ptz` router (`app/routers/ptz.py`) inspects the exception message for connection-error indicators (`Connection aborted`, `Connection reset`, `ConnectionError`, `RemoteDisconnected`). When one matches, it transparently drops the session, reconnects, and retries the same action once. If that retry also fails — or the failure wasn't a recognized connection error — the request returns `503` with the underlying error message.

## ONVIF Protocol Details

minicam uses `ContinuousMove` from the ONVIF PTZ WSDL:

```xml
<tptz:ContinuousMove>
  <tptz:ProfileToken>000</tptz:ProfileToken>
  <tptz:Velocity>
    <tt:PanTilt x="0.0" y="0.5"/>   <!-- up -->
  </tptz:Velocity>
</tptz:ContinuousMove>
```

`ProfileToken` is not actually hardcoded to `"000"` — it is fetched dynamically from `GetProfiles()` the first time the service connects and reused for every subsequent command. Only `PanTilt` velocity is ever sent; `Zoom` is never included in `ContinuousMove` requests.

A `Stop` action sends:

```xml
<tptz:Stop>
  <tptz:ProfileToken>000</tptz:ProfileToken>
  <tptz:PanTilt>true</tptz:PanTilt>
  <tptz:Zoom>true</tptz:Zoom>
</tptz:Stop>
```

## UI Usage

In the dashboard, hold a direction button to move continuously. Releasing the button sends a `stop` action automatically.
