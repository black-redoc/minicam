# minicam

Multi-camera IPCAM dashboard (FastAPI backend), with session auth, per-camera
authorization, and a hardened Cloudflare Tunnel / Access path for WAN access.

## Quick Start

### Create the JWT secret

The app refuses to start without `MINICAM_JWT_SECRET`. Create `.env` with a
fresh random secret and export it into your shell (`fastapi dev` does not read
`.env` on its own):

```bash
cp .env.example .env && chmod 600 .env
secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sed -i.bak "s/^MINICAM_JWT_SECRET=.*/MINICAM_JWT_SECRET=$secret/" .env && rm .env.bak
set -a; . ./.env; set +a
```

See [Initial Setup](app/docs/configuration.md#initial-setup-creating-minicam_jwt_secret)
for details and how to rotate it.

### Start the development server

```bash
uv run fastapi dev
```

Visit http://localhost:8000

## Checks

CI (`.github/workflows/ci.yml`) runs on every push to `master` and every pull
request. To run the same checks locally:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app main.py
uv run pytest
npm ci --ignore-scripts && npm run lint && npm run typecheck
```

## Project Structure

```
main.py                 App entry point: lifespan starts each camera's RTSP
                        capture, PTZ service and detection worker; mounts routers
app/
  routers/              HTTP/WebSocket endpoints: auth, dashboard, stream, ptz,
                        setup (cameras, detection), docs
  services/             Camera capture/decoding, JPEG encoding, ONVIF PTZ and
                        discovery, auth, authorization, LAN/WAN classification,
                        Cloudflare Access verification, stream tokens
    detection/          Pluggable object detection (ONNX backend, NMS, worker)
  buffers/              Per-camera frame buffer shared by capture and viewers
  models/               Pydantic and domain models (config, auth, frames)
  repositories/         SQLite access: users, sessions, permissions, wan_access
  db/                   Connection setup and SQL migrations
  middleware/           Security headers
  cli/                  `create_user` (create the first admin from the shell)
  settings.py           config.toml loader (cameras, detection)
  security_settings.py  Environment-based security config (.env)
  dependencies.py       FastAPI dependency wiring for the security layer
  state.py              Process-wide singletons (cameras, buffers, workers)
  templates/            Jinja2 pages (dashboard, login, admin, AI detection, docs)
  static/               CSS, images and plain JS (type-checked via tsconfig.json)
  docs/                 User docs, served in-app at /doc/<page>
deploy/minicam.service  systemd unit for the device
tests/                  unit, integration, e2e and benchmarks
config.toml.example     Camera config template (real config.toml is gitignored)
.env.example            Security env vars template (real .env is gitignored)
spec.md                 Product and architecture spec
migrate_env_to_toml.py  One-off migration from the old .env camera config
```

At runtime the app also creates the SQLite database (`MINICAM_DB_PATH`),
writes `config.toml` whenever cameras or detection settings are edited from the
UI (it starts with defaults if the file is missing), and stores uploaded or
fetched detection models in `app/data/models/`.

## Deployment

Copy `.env.example` to `.env`, generate `MINICAM_JWT_SECRET` as described in
[Initial Setup](app/docs/configuration.md#initial-setup-creating-minicam_jwt_secret),
and fill in the remaining values (see the comments in that file, or
[Configuration](app/docs/configuration.md) for the full reference), then enable the app as a systemd service:

```bash
sudo cp deploy/minicam.service /etc/systemd/system/minicam.service
sudo systemctl daemon-reload
sudo systemctl enable --now minicam.service
```

Replace `<user>` and the checkout path in the unit first. The unit is
sandboxed: everything is read-only except the checkout and uv's cache, so if
you keep the checkout or `MINICAM_DB_PATH` somewhere else, update
`ReadWritePaths` too. After a change, check that the app still starts
(`journalctl -u minicam -e`) and review the score with
`systemd-analyze security minicam.service`.

`cloudflared` (the tunnel that exposes this instance to WAN through
Cloudflare Access) is supervised separately. To set it up from scratch
(install, tunnel, hostname, Access policy, `.env` values), follow
[Cloudflare Access & Tunnel → Setup](app/docs/cloudflare.md#setup-step-by-step). If you installed it with
`cloudflared service install <token>`, it already registered its own
`cloudflared.service` unit (enabled, `Restart=on-failure`) — there is
nothing to add in this repo for it. Check with:

```bash
systemctl status cloudflared.service
```

See [Cloudflare Access & Tunnel](app/docs/cloudflare.md) for how the tunnel,
Access, and the app's own login fit together.
