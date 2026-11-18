# 0013 Signed updates from GitHub releases — Status: Accepted
Date: 2026-11-18

## Context
A device runs the minicam its SD image shipped with (ADR 0005); a new release means flashing a
new card and setting the device up again. Admins want the device to update itself to a new
release tag from minicam-mobile. minicam is unprivileged and sandboxed, the code and its venv
live in `/opt/minicam` next to the device's state, and the Pi may be reachable only through the
tunnel. Installing code as root from the internet must not trust GitHub alone.

## Decision
- **Bundle**: every `vX.Y.Z` tag publishes, next to the image, `minicam-vX.Y.Z-linux-arm64.tar.gz`:
  the image's `/opt/minicam` (code, `.python`, `.venv`) taken from the pi-gen rootfs, without
  device state. CI signs it with minisign; the trusted comment is `minicam vX.Y.Z linux-arm64`.
  The secret key is the `MINISIGN_SECRET_KEY` repository secret; the public key is
  `deploy/update/minicam-release.pub`, installed root-owned in the image.
- **Check**: minicam asks `api.github.com/repos/black-redoc/minicam/releases/latest` (fixed
  repo, at most every 6 h, or on an admin's explicit refresh after 60 s) and offers a release
  only if it is newer than `pyproject.toml` and has the bundle.
- **Routes** (admin, LAN or WAN): `GET /api/system/update` answers `{available, current, latest,
  update_available, state, version, message, checked_at}`; `POST /api/system/update {version}`
  accepts only the offered release and writes `/opt/minicam/.update/request.json`.
- **Root helper** `minicam-update` (stdlib Python, started by `minicam-update.path`, like the
  tunnel helper of ADR 0008) re-validates the request, refuses downgrades, downloads the bundle
  and its signature from the fixed release URL, verifies them with `minisign` and the pinned key
  (trusted comment must name the version), extracts it next to `/opt/minicam`, stops minicam,
  backs up the database, swaps the code entries (state stays: `.env`, `config.toml`, `*.db`,
  `tunnel.json`, `central.json`, `.tunnel/`, `.update/`, `app/data/*`), starts minicam and waits
  for `/api/health`. If minicam does not come up it restores the old code and database. On
  success it also installs the new release's helpers and systemd units. Progress goes to
  `.update/status.json`: `downloading`, `verifying`, `installing`, `restarting`, `done`, `error`.
- Devices flashed before this release have no helper: they need one new image first.

## Alternatives considered
- **apt repository**: signed by apt, but needs a hosted repo and Debian packaging of a venv.
- **`git pull` + `uv sync` on the device**: needs git, uv, network to PyPI and possibly a
  compiler on the Pi; no signature on the code.
- **SHA-256 from the same release only**: a compromised account or release could ship anything.
- **Automatic updates**: the admin decides when the device restarts (it may be recording).

## Consequences
- An update downloads ~200 MB and needs that much free space twice for a moment.
- Losing the minisign secret key means no more updates until devices get a new image with a new
  public key; leaking it lets an attacker sign bundles (they still need GitHub to serve them).
- A release whose migrations break the old code can't be rolled back by hand except with the
  database backup the helper keeps until the next update.
