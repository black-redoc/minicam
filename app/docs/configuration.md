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

Unlike camera config, the security layer (sessions, stream tokens, WAN access) is configured entirely through **environment variables**, read once at startup by `app/security_settings.py`. See `.env.example` in the project root for the authoritative, up-to-date list with generation instructions — the table below mirrors it.

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `MINICAM_JWT_SECRET` | **yes** | — | Secret used to sign session and stream-token JWTs. The app refuses to start without it. See [Initial Setup](#initial-setup-creating-minicam_jwt_secret). |
| `MINICAM_SESSION_TTL_SECONDS` | no | `86400` (24h) | How long a login session stays valid before it must be renewed via a new login. |
| `MINICAM_STREAM_TOKEN_TTL_SECONDS` | no | `30` | Lifetime of the short-lived token that authorizes one WebSocket handshake. See [Video Streaming](/doc/stream). |
| `MINICAM_DB_PATH` | no | `app/minicam.db` | Path to the SQLite database storing users, sessions, camera permissions, and WAN access flags. |
| `MINICAM_TRUSTED_PROXIES` | no | `""` (empty) | Comma-separated list of proxy IPs. Requests arriving through one of these are always classified as WAN — see [Authentication & Authorization](/doc/auth). |
| `MINICAM_CF_ACCESS_TEAM_DOMAIN` | required for WAN | — | Cloudflare Access team domain, used to fetch the JWKS that verifies the `Cf-Access-Jwt-Assertion` header on WAN requests. |
| `MINICAM_CF_ACCESS_AUD` | required for WAN | — | Audience (AUD) tag of the Cloudflare Access application protecting this tunnel hostname. |
| `MINICAM_MODEL_CATALOG_URL` | no | latest [minicam-models](https://github.com/black-redoc/minicam-models) release `manifest.json` | Manifest the AI Detection model catalog lists and installs from. Point it at a tagged release to pin model versions. See [Object Detection](/doc/detection). |

## Initial Setup: Creating `MINICAM_JWT_SECRET`

Every new install needs its own `MINICAM_JWT_SECRET` before the first start — the app refuses to boot without it (`RuntimeError: MINICAM_JWT_SECRET is not set`). It signs session and stream-token JWTs (HS256), so anyone who knows it can forge a login: generate a fresh random value per device and never reuse the placeholder from `.env.example`.

1. Create `.env` from the template and lock down its permissions (it is already in `.gitignore` — never commit it):

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

2. Generate a 64-character hex secret (32 random bytes) and write it into `.env`:

   ```bash
   secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   sed -i.bak "s/^MINICAM_JWT_SECRET=.*/MINICAM_JWT_SECRET=$secret/" .env && rm .env.bak
   grep -c '^MINICAM_JWT_SECRET=[0-9a-f]\{64\}$' .env   # prints 1
   ```

   `openssl rand -hex 32` produces an equivalent value if Python isn't handy.

3. Make sure the process actually receives it:
   - **systemd (production):** `deploy/minicam.service` loads `.env` through `EnvironmentFile=`, so `sudo systemctl restart minicam.service` is enough.
   - **Development:** `uv run fastapi dev` does **not** read `.env` by itself. Export it into your shell first:

     ```bash
     set -a; . ./.env; set +a
     uv run fastapi dev
     ```

**Rotating the secret.** Replace the value in `.env` and restart the app. Every existing session cookie and stream token stops verifying immediately, so all users are logged out and must sign in again — which is also the right move if the secret ever leaks.

## Bootstrapping the First Admin

On a fresh install (empty database), open `http://<device-ip>:8080/login` from the local network: instead of the sign-in form it shows **Create admin account**. Enter an email and a password (min. 8 characters); that account becomes the administrator and you are signed in. Every later visit to `/login` shows only the normal sign-in form. This form is refused over WAN (the Cloudflare tunnel), so do this step on the device's network.

Alternatively, create the first admin out-of-band with the CLI:

```
uv run python -m app.cli.create_user --email admin@example.com --admin
```

Omit `--password` to be prompted interactively instead of passing it on the command line. See [Authentication & Authorization](/doc/auth) for how sessions and permissions work after that first login.
