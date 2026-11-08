# 0009 Pair with the central dashboard from the app — Status: Accepted
Date: 2026-11-08

## Context
In central mode (minicam-admin ADRs 0007/0008) minicam reads `MINICAM_CENTRAL_URL` and
`MINICAM_DEVICE_TOKEN` from `.env` at start: connecting a device meant copying the dashboard's
device token and editing `.env` over SSH. The owner wants every device setup step done from
minicam-mobile, with no SSH or keyboard on the Pi.

## Decision
- **Pairing code:** the dashboard shows a one-time code for the site (minicam-admin ADR 0011). In
  the app (Admin → Central dashboard, admin + LAN only) the admin enters the dashboard address and
  the code; `POST /api/system/central` `{url, pairing_code}` makes minicam call
  `POST <url>/api/device/pair` itself, so the device token never passes through a person or the
  phone. `GET` reports `{connected, url, source, last_synced_at}`; `DELETE` disconnects.
- **Storage:** `central.json` (0600) next to `config.toml`, read by the security settings when
  `.env` sets neither variable. `.env` always wins: an install configured there answers `409` to
  the app and is never changed by it.
- **No restart:** `CentralRuntime` owns the entitlement sync and usage report loops; boot starts it
  from the settings, pairing restarts it with the new token, unpairing stops it (flushing usage).
- **Rotation** is pairing again: the dashboard rotates the credential (old token valid 10 h).
- The URL must be `https://` with no path (plain `http` only to localhost, like `.env`); errors
  from the dashboard are shown as is (`502`), never the code or the token.

## Alternatives considered
- **Paste the token in the app / scan a QR:** the token would sit on the phone (minicam-admin ADR
  0011).
- **Write `.env` and restart minicam:** minicam cannot restart itself cleanly; `.env` stays the
  manual-install path.

## Consequences
- After unpairing, WAN access is managed on the device again, starting from the flags the last
  sync left.
- The image keeps `central.json` out of the built image (`check-image.sh`).
