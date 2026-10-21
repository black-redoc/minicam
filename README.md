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

### Deploy to FastAPI Cloud

Sign up and log in at https://fastapicloud.com, then deploy with:

```bash
uv run fastapi deploy
```

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

- `main.py` - Your FastAPI application
- `pyproject.toml` - Project dependencies

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
