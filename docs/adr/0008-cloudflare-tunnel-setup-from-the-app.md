# 0008 Cloudflare Tunnel setup from the app — Status: Accepted
Date: 2026-10-29

## Context
WAN access needs cloudflared on the Pi, a tunnel routed to `127.0.0.1:8080`, a DNS record, a
Cloudflare Access application and policy, and `MINICAM_CF_ACCESS_TEAM_DOMAIN` /
`MINICAM_CF_ACCESS_AUD` in `.env` (app/docs/cloudflare.md). Today that is a manual SSH session
and the dashboard. The image (ADR 0005) ships no cloudflared and no credential. We want an admin
to do the whole setup from minicam-mobile (minicam-mobile ADR 0009).

minicam runs unprivileged and sandboxed (`NoNewPrivileges`, `ProtectSystem=strict`), so it cannot
install packages or run system services itself.

## Decision
- **Cloudflare API calls happen in the app**, with an API token the admin pastes there and that
  is used only in memory (minicam-mobile ADR 0009). The device never receives an account-wide
  credential: the app sends minicam only the tunnel token, the public hostname, the Access team
  domain and the application's AUD tag.
- **minicam endpoints** (admin + LAN only, like Wi-Fi in ADR 0007):
  - `GET /api/system/tunnel` → `{available, state, hostname, message}`; `state` is
    `none | installing | running | error`.
  - `POST /api/system/tunnel` `{tunnel_token, hostname, team_domain, aud}` → 202. Validates
    every field strictly, stores the Access settings and asks the helper to install.
  - `DELETE /api/system/tunnel` → stops the tunnel and clears the Access settings.
- **Access settings at runtime:** stored by minicam in `tunnel.json` (0600, next to
  `config.toml`). `MINICAM_CF_ACCESS_*` environment values still win, so manual installs keep
  working. Saving clears the settings caches, so no restart is needed. While a tunnel is set up,
  `127.0.0.1` is always a trusted proxy: tunnel traffic must never be classified as LAN.
- **Privileged helper:** `minicam-tunnel.path` watches `/opt/minicam/.tunnel/request.json`
  (written by minicam, 0600) and starts the root oneshot `minicam-tunnel.service`, which runs a
  small stdlib-only Python script (`/usr/local/sbin/minicam-tunnel`). It:
  1. re-validates the request (it trusts nothing minicam wrote);
  2. installs cloudflared from Cloudflare's signed apt repository on first use, with the
     repository key pinned by SHA-256 (like `.github/scripts/raspberry-pi-os-setup.sh`);
  3. writes `TUNNEL_TOKEN` to `/etc/minicam/tunnel.env` (root, 0600) and enables
     `minicam-cloudflared.service` (`cloudflared --no-autoupdate tunnel run`, `DynamicUser=yes`,
     sandboxed); on `remove` it disables the unit and deletes the file;
  4. reports progress in `/opt/minicam/.tunnel/status.json` and deletes the request.
  The image ships these units and the script; manual installs can copy them from `deploy/`.
- **Updates:** cloudflared is an apt package, updated with the OS (`apt upgrade`); re-sending
  the setup also upgrades it. `--no-autoupdate` keeps the binary from replacing itself. Automatic
  updates are decided with M3.

## Alternatives considered
- **Cloudflare API calls on the Pi:** testable in Python, but the device would hold an API token
  with rights over the whole account. Rejected.
- **Preinstall cloudflared in the image:** no network or apt at setup time, but every image
  ships a package most users may never use; chosen against by the user. The helper keeps the
  install path small and fixed.
- **`cloudflared service install <token>`:** puts the token in the unit's `ExecStart`, visible
  in `ps` and `systemctl cat`; our own unit reads it from a root-only environment file.
- **polkit + `systemctl` from minicam:** works, but a path unit needs no D-Bus access from the
  sandbox and limits minicam to writing one file.

## Consequences
- The setup needs internet on the Pi during installation (apt) and takes about a minute.
- A new root code path exists; it accepts one JSON file with three fixed actions and validates it.
- The Access settings can now change at runtime; tests cover that both caches are cleared.
- Manual installs that set `MINICAM_CF_ACCESS_*` in `.env` are unaffected.
