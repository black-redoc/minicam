# PTZ Control

minicam controls Pan-Tilt-Zoom via the **ONVIF** protocol (SOAP over HTTP). PTZ is available for any camera whose `onvif_port` is configured.

## Endpoint

```
POST /api/ptz/{camera_name}/{action}
```

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
{ "ok": true }
```

On error (camera not found, ONVIF unreachable):

```json
{ "detail": "Camera 'entrada' not found" }
```

## Speed

Movement speed is fixed at **0.5** in both X (pan) and Y (tilt) axes. This maps directly to the ONVIF `ContinuousMove` `Velocity` parameter (range 0.0–1.0).

To change the speed, edit `PTZService` in `app/services/ptz.py`.

## Connection Lifecycle

`PTZService` connects **lazily** — the first PTZ command triggers the ONVIF handshake. Subsequent commands reuse the same connection.

If the ONVIF service becomes unreachable (e.g., camera reboot), the service automatically reconnects on the next command.

## ONVIF Protocol Details

minicam uses `ContinuousMove` from the ONVIF PTZ WSDL:

```xml
<tptz:ContinuousMove>
  <tptz:ProfileToken>000</tptz:ProfileToken>
  <tptz:Velocity>
    <tt:PanTilt x="0.0" y="0.5"/>   <!-- up -->
    <tt:Zoom x="0.0"/>
  </tptz:Velocity>
</tptz:ContinuousMove>
```

A `Stop` action sends:

```xml
<tptz:Stop>
  <tptz:ProfileToken>000</tptz:ProfileToken>
  <tptz:PanTilt>true</tptz:PanTilt>
  <tptz:Zoom>false</tptz:Zoom>
</tptz:Stop>
```

## UI Usage

In the dashboard, hold a direction button to move continuously. Releasing the button sends a `stop` action automatically.
