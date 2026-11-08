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
uv run fastapi dev --host 0.0.0.0
```

Visit `http://<this-machine's-LAN-IP>:8000` (e.g. `http://192.168.1.20:8000`).
`--host 0.0.0.0` makes the server reachable from the local network; without it
`fastapi dev` only listens on `127.0.0.1`. Use the LAN IP rather than
`localhost`/`127.0.0.1`: with `MINICAM_TRUSTED_PROXIES=127.0.0.1` (the
`.env.example` default, needed for the Cloudflare tunnel) every request from
`127.0.0.1` is treated as WAN, so first-run setup is refused there and WAN
rules apply.

### First login

1. On a fresh database, `/login` shows **Create admin account**: create your
   admin user (see [Bootstrapping the First Admin](app/docs/configuration.md#bootstrapping-the-first-admin)).
2. Admins don't see any camera by default. Open **`/admin`** and, in your user's
   card, tick **View** and **PTZ** for each camera (each checkbox saves
   immediately). Reload the dashboard and the streams connect.

## Checks

CI (`.github/workflows/ci.yml`) runs on every push to `master` and every pull
request. To run the same checks locally:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app main.py
uv run pytest
npm ci --ignore-scripts && npm run lint && npm run typecheck
```

CI also runs the test suite on a Raspberry Pi OS (64-bit) userland: Debian
trixie arm64 plus Raspberry Pi's apt repository
(`.github/scripts/raspberry-pi-os-setup.sh`), as the unprivileged `pi` user.
It catches arm64 wheel and Pi OS package problems, but not the Pi 5 kernel
(16 KB pages) or its hardware.

## Project Structure

```
main.py                 App entry point: lifespan starts each camera's RTSP
                        capture, PTZ service and detection worker; mounts routers
app/
  routers/              HTTP/WebSocket endpoints: auth, dashboard, stream, ptz,
                        setup (cameras, detection, model catalog), health, docs,
                        mobile (JSON camera list, Access handoff)
  services/             Camera capture/decoding, JPEG encoding, ONVIF PTZ and
                        discovery, auth, authorization, LAN/WAN classification,
                        Cloudflare Access verification, stream tokens,
                        minicam-models catalog parsing
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
fetched detection models (including ones installed from the
[minicam-models](https://github.com/black-redoc/minicam-models) catalog on the
AI Detection page) in `app/data/models/`.

## Deployment

### From the Raspberry Pi OS image

Each [release](https://github.com/black-redoc/minicam/releases) ships
`minicam-vX.Y.Z-raspios-trixie-arm64.img.xz`: Raspberry Pi OS Lite (64-bit)
with minicam already installed as a systemd service, for Raspberry Pi 4 and 5.

#### 1. Flash the SD card

Use Raspberry Pi Imager with this repository's Imager catalog, so Imager offers
OS customisation (user, Wi-Fi, SSH, hostname) as with the stock image. The
catalog always points at the latest release:

```
https://github.com/black-redoc/minicam/releases/latest/download/minicam-imager.json
```

Quit Imager, then open it with the catalog. On macOS:

```bash
open -a "Raspberry Pi Imager" --args --repo https://github.com/black-redoc/minicam/releases/latest/download/minicam-imager.json
# if Imager starts without it:
"/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager" --repo https://github.com/black-redoc/minicam/releases/latest/download/minicam-imager.json
```

On Linux and Windows pass the same `--repo <url>` to `rpi-imager`. From the
Imager window instead: **App Options** (Spanish UI: **Añadir opciones**) →
**Content Repository** (**Repositorio de contenidos**) → **Edit** → **Use custom
URL**, paste the URL, **Apply & Restart**.

Then pick your Pi (4 or 5), **minicam vX.Y.Z** as the OS and the SD card. In
the customisation, turn on **SSH** with a user and password: the app does the
whole setup, but SSH is the way in if something needs fixing. ("Use custom"
with the downloaded `.img.xz`, checked against its `.sha256`, also works, but
Imager disables customisation for local files.)

#### 2. First boot

Connect the Pi to your router with an Ethernet cable and power it on. On first
boot it creates its own `/opt/minicam/.env` with a new `MINICAM_JWT_SECRET`
(none is shipped in the image) and, unless you chose a hostname in Imager,
renames itself `minicam-XXXX` (the last four hex digits of its MAC address).
After 2-3 minutes it answers at `http://minicam-XXXX.local:8080` and is
announced on the LAN as `_minicam._tcp`.

#### 3. Set it up from minicam-mobile

Everything else is done from the [minicam-mobile](https://github.com/black-redoc/minicam-mobile)
app, with the phone on the same network as the Pi:

1. **Admin account** (within 30 minutes of power-on; power-cycle the Pi to
   reopen it): on the login screen, **Find minicam on my network**, pick the
   device, **Blink a code on the device**, then type the 4 digits the green LED
   blinks (N blinks per digit, a longer pause between digits), your email and a
   password. The web page at `/login` offers the same.
2. **Cameras**: ⋮ → Camera settings (ONVIF discovery included).
3. **Remote access** (optional): ⋮ → Remote access, with a Cloudflare API token;
   the app creates the tunnel, DNS record and Access application, and the Pi
   installs `cloudflared` itself ([Cloudflare](app/docs/cloudflare.md#setup-from-the-mobile-app)).
4. **Central dashboard** (optional): in minicam-admin's Sites, **Pair device**
   shows a one-time code; in the app, ⋮ → Central dashboard, enter the
   dashboard's address and the code. The Pi fetches its own device token
   ([Central mode](app/docs/auth.md#central-mode)).

minicam lives in `/opt/minicam` and runs as the `minicam` system user with the
same sandboxed unit as below (`deploy/minicam.service` plus the
`minicam.service.d/image.conf` drop-in). Values set by hand in
`/opt/minicam/.env` (as root, then `sudo systemctl restart minicam`) take
precedence over the app.

Updating minicam on a flashed Pi is not automated yet: flash the new release,
or copy the new release's code over `/opt/minicam` over SSH.

### Manual install

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
