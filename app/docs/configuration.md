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

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | — | Unique identifier used in URLs and UI |
| `host` | string | yes | — | Camera IP address |
| `rtsp_port` | integer | no | `554` | RTSP port (typically 554) |
| `onvif_port` | integer | no | `80` | ONVIF HTTP port (typically 80 or 8000) |
| `user` | string | no | `"admin"` | Camera login username |
| `password` | string | no | `""` | Camera login password |
| `channel` | integer | no | `1` | Channel number (1 for single-channel cameras) |
| `stream` | string | no | `"main"` | `"main"` for HD stream, `"sub"` for sub-stream |

Only `name` and `host` are truly required — every other field falls back to its default if omitted from `config.toml`.

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

> **Security note:** Because `config.toml` stores camera passwords in plaintext, every write (`app/services/config_writer.py`) sets the file's permissions to `chmod 600` (owner read/write only).

## Security Settings

Unlike camera config, the security layer (sessions, stream tokens, WAN access) is configured entirely through **environment variables**, read once at startup by `app/security_settings.py`.

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `MINICAM_JWT_SECRET` | **yes** | — | Secret used to sign session and stream-token JWTs. The app refuses to start without it. |
| `MINICAM_SESSION_TTL_SECONDS` | no | `86400` (24h) | How long a login session stays valid before it must be renewed via a new login. |
| `MINICAM_STREAM_TOKEN_TTL_SECONDS` | no | `30` | Lifetime of the short-lived token that authorizes one WebSocket handshake. See [Video Streaming](/doc/stream). |
| `MINICAM_DB_PATH` | no | `app/minicam.db` | Path to the SQLite database storing users, sessions, camera permissions, and WAN access flags. |
| `MINICAM_TRUSTED_PROXIES` | no | `""` (empty) | Comma-separated list of proxy IPs. Requests arriving through one of these are always classified as WAN — see [Authentication & Authorization](/doc/auth). |

## Bootstrapping the First Admin

There is no self-service signup and every admin-only endpoint requires an existing admin, so the first account must be created out-of-band with the CLI:

```
uv run python -m app.cli.create_user --email admin@example.com --admin
```

Omit `--password` to be prompted interactively instead of passing it on the command line. See [Authentication & Authorization](/doc/auth) for how sessions and permissions work after that first login.
