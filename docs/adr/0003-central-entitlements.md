# 0003 WAN entitlements from the central dashboard — Status: Accepted
Date: 2026-10-26

## Context
WAN access (`wan_access`) was set by hand on each Pi. Billing and manual blocks now live in the
central dashboard ([minicam-admin](https://github.com/black-redoc/minicam-admin)), which decided the
boundary in its ADR 0007 (`docs/adr/0007-minicam-pulls-entitlements.md`) and spec 011
(`docs/specs/011-entitlements.md`). This ADR records minicam's side only; the full reasoning and
alternatives are there.

## Decision
- Central mode is on when both `MINICAM_CENTRAL_URL` (HTTPS; plain HTTP only to localhost) and
  `MINICAM_DEVICE_TOKEN` are set; one without the other refuses to start. Without them minicam
  behaves as before (standalone).
- In central mode a background task (`app/services/entitlements.py`) calls
  `GET /api/device/entitlements` every 60 s with the device token, parses the answer strictly and
  rewrites `wan_access` for every local user, matching `minicamUser` to `users.email`
  case-insensitively; anyone not listed with `wan: true` is inactive. The sync time goes to
  `entitlement_sync` (migration 0005).
- Any failure keeps the cache. `WanAccessGateService` allows WAN only while the last sync is at most
  30 min old and not in the future, so a Pi cut off from the dashboard fails closed for WAN. LAN is
  never affected. Open WAN streams close on the next 10 s re-check (`StreamAccessService`).
- `POST /api/auth/wan-access` answers 409 in central mode; the Admin page shows that message.
- `httpx` (already installed through `fastapi[standard]`) is pinned as a direct dependency.

## Consequences
- A block or payment reaches the Pi within about 70 s; a new local user has no WAN until the next sync.
- An outage of the dashboard or the tunnel longer than 30 min stops WAN for every user of the site.
- The device token is a secret: env only, kept out of `repr()` and logs.
