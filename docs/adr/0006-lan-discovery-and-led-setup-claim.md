# 0006 LAN discovery over mDNS and LED setup claim — Status: Accepted
Date: 2026-10-28

## Context
A freshly flashed device (ADR 0005) has no users. Today `POST /api/auth/setup` creates the first
admin from any LAN client while the users table is empty, and the user has to find the Pi's IP by
hand. With a ready-to-flash image, many devices sit unclaimed on home networks, so "first LAN
client wins" is too weak, and minicam-mobile should find the device on its own
(minicam-mobile ADR 0008). The Pi has no screen, but every Pi 4 and Pi 5 has a green ACT LED.

## Decision
- **Discovery:** a static Avahi service file `/etc/avahi/services/minicam.service` advertises
  `_minicam._tcp` on port 8080 with TXT `api=1`. It is always on, also after setup, so the apps
  find the device on the LAN for normal use. Android uses `NsdManager`, iOS `NWBrowser` (no
  multicast entitlement needed). No new Python dependency.
- **Setup status:** the existing `GET /api/auth/setup` (public, used by the login page) keeps
  `setup_required` and adds `claim` (`"none"` or `"led"`), `window_open` and
  `window_seconds_left`; for WAN requests `window_open` is always false. The app shows "ready to set up", "restart the
  Pi to set it up" or nothing.
- **Setup window:** open for 30 min after the service starts, only while no user exists. A restart
  (power cycle) reopens it. Outside the window every setup endpoint answers 403.
- **LED claim** (only when `MINICAM_SETUP_CLAIM=led`, set by the image; default `none` keeps
  manual installs and development unchanged):
  1. `POST /api/auth/setup/claim` (LAN, window open) creates a random 4-digit code, each digit 1-9
     (`secrets`), valid 5 min, replacing any previous code. At most one new code per 30 s and 5
     codes per window (429 / 403 after).
  2. The ACT LED blinks it in a loop: per digit N short blinks (250 ms on / 250 ms off), 1.5 s
     pause between digits, 4 s pause before repeating.
  3. `POST /api/auth/setup` requires `setup_code`; compared in constant time. 3 wrong codes
     invalidate it (the user asks for a new one). On success the admin is created, the code is
     cleared and the LED trigger is restored to its original value.
- **LED access without loosening the sandbox:** a udev rule gives group `minicam` write access to
  only `trigger` and `brightness` of the `ACT` LED; the image drop-in adds those sysfs paths to
  `ReadWritePaths`. If systemd cannot re-open them under `ProtectKernelTunables`, fall back to a
  minimal root `minicam-led.service` that blinks a code read from a runtime file.
- The code is never logged or returned by any endpoint.

## Alternatives considered
- **Time window only:** anyone on the Wi-Fi within 30 min can claim the device.
- **Code file on the boot partition:** secure, but the user must take the SD card out to read it.
- **Pi 5 power button:** nice physical proof, but the Pi 4 has no button.
- **Custom UDP broadcast "ping":** same result as mDNS, but iOS needs Apple's multicast entitlement.
- **HTTP sweep of the subnet:** slow, does not scale past a /24, drains battery.

## Consequences
- Guessing without seeing the LED: 5 codes × 3 tries over 6561 codes ≈ 0.23 % per window, and a
  new window needs physical access (power cycle).
- A LAN attacker can burn the 5 codes (denial of setup only); the owner restarts the Pi.
- On LAN the code and the new password travel over plain HTTP (KNOWN_ISSUES KI-1).
- Reading the code takes ~10-25 s; the app shows how to count the blinks.
