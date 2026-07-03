# Configuration

minicam reads its configuration from `config.toml` in the project root at startup.

## Full Example

```toml
[app]
host = "0.0.0.0"
port = 8080
debug = false

[[cameras]]
name = "entrada"
host = "192.168.1.100"
rtsp_port = 554
onvif_port = 80
user = "admin"
password = "secret"
channel = 1
stream = "main"

[[cameras]]
name = "patio"
host = "192.168.1.101"
rtsp_port = 554
onvif_port = 80
user = "admin"
password = "secret"
channel = 1
stream = "sub"
```

## `[app]` section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"0.0.0.0"` | Bind address for the HTTP server |
| `port` | integer | `8080` | TCP port |
| `debug` | boolean | `false` | Enable FastAPI debug mode |

## `[[cameras]]` entries

Each `[[cameras]]` block defines one IP camera. You may add as many as needed.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique identifier used in URLs and UI |
| `host` | string | yes | Camera IP address |
| `rtsp_port` | integer | yes | RTSP port (typically 554) |
| `onvif_port` | integer | yes | ONVIF HTTP port (typically 80 or 8000) |
| `user` | string | yes | Camera login username |
| `password` | string | yes | Camera login password |
| `channel` | integer | yes | Channel number (1 for single-channel cameras) |
| `stream` | string | yes | `"main"` for HD stream, `"sub"` for sub-stream |

## RTSP URL Construction

The RTSP URL is built automatically from the camera fields:

```
rtsp://{user}:{password}@{host}:{rtsp_port}/h264/ch{channel}/{stream}/av_stream
```

For example, with the `entrada` camera above:

```
rtsp://admin:secret@192.168.1.100:554/h264/ch1/main/av_stream
```

> **Note:** This URL format is compatible with most Hikvision-compatible IPCAM firmware. If your camera uses a different path, you can override `rtsp_url` directly (not yet exposed in the UI).

## Applying Changes

minicam reads `config.toml` at startup. Cameras edited via the UI or API automatically rewrite the file and restart the affected camera services without requiring a full server restart.
