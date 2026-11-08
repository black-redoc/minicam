# 0010 Install remote access from the central pairing response — Status: Accepted
Date: 2026-11-08

## Context
Pairing with the central dashboard (ADR 0009) installs the device token; the Cloudflare Tunnel
is set up separately from the app with a pasted API token (ADR 0008). minicam-admin ADR 0012 makes
the dashboard create the tunnel, DNS record and Access application and return them when the code
is redeemed, so one code from the dashboard sets up both.

## Decision
- `POST /api/system/central` `{url, pairing_code}` (admin + LAN only, unchanged) reads the
  optional `tunnel: {tunnel_token, hostname, team_domain, aud}` from the pair response.
  - It is validated with the same rules as `POST /api/system/tunnel` (ADR 0008).
  - Then it goes through the same path: Access settings in `tunnel.json`, a request to the
    privileged helper (`minicam-tunnel.path`), settings caches cleared.
  - The response stays `200` with the ADR 0009 status plus `tunnel: {state, hostname, message,
    replaced}` (`installing | skipped | error`, or `null` when the dashboard sent none), so
    existing apps keep working; the app polls `GET /api/system/tunnel` until `running` or
    `error`. Invalid settings or no helper → `error`; the pairing itself still holds.
- **Source of the tunnel:** `tunnel.json` records `source: "central" | "app"`.
  `GET /api/system/tunnel` reports it. While the source is `central`, `POST` and `DELETE
  /api/system/tunnel` answer `409` (the dashboard owns it).
- **Precedence:** `MINICAM_CF_ACCESS_*` in `.env` still wins. Then minicam keeps the pairing,
  skips the tunnel and reports `tunnel: {state: "skipped"}`. A tunnel the app set up
  (`source: app`) is replaced by the central one, which the response reports.
- **Unpairing** (`DELETE /api/system/central`) also removes a `central` tunnel and its Access
  settings. WAN through the dashboard's account without its entitlements would fail closed anyway.
- **Re-pairing** reinstalls the returned tunnel; the helper is idempotent.
- The tunnel token is never logged, never returned by any endpoint and lives only in
  `/etc/minicam/tunnel.env` (root, 0600), as in ADR 0008.

## Alternatives considered
- **The app forwards the tunnel to `POST /api/system/tunnel`:** the tunnel token would pass
  through the phone; minicam already holds the redeem response.
- **Keep the tunnel when unpairing:** leaves a tunnel in someone else's Cloudflare account with
  nothing to authorise it.

## Consequences
- A central install needs internet during pairing (apt for cloudflared on first use, about a
  minute), as the app path does today.
- A pairing can succeed while the tunnel fails; the two states are reported separately and the
  app shows both.
- Security: a dashboard can now point the device's WAN at a Cloudflare account it controls. The
  boundary is unchanged: only an admin on the LAN can pair. minicam-mobile ADR 0011 makes the app
  show the dashboard host for confirmation before sending the code.
