# 0002 Sub-stream as the only camera stream — Status: Accepted
Date: 2026-10-24

## Context
- Every camera was read from its main stream by default (`stream = "main"`),
  typically 2-4 Mbit/s at full resolution. The grid opens one live stream per
  camera, so remote viewing through the Cloudflare Tunnel multiplied that
  bitrate on the home uplink and the viewer's mobile data.
- Cameras also publish a sub-stream (typically 0.3-0.5 Mbit/s, e.g. 640x360),
  reachable with the same URL scheme (`/h264/ch{channel}/sub/av_stream`) on
  both EZVIZ and Hikvision.
- ADR 0001 planned automatic main/sub switching (phase 4), which needs a
  second RTSP session per camera and a link-speed heuristic.
- Detection models resize frames to their input size (e.g. 640 px), so a
  sub-stream loses little detection accuracy.

## Decision
- minicam always reads the camera's sub-stream: live view, detection and event
  recordings all use it. The per-camera `stream` setting is removed from the
  config, the API and the UI.
- A `stream` key left in an existing `config.toml` is ignored (pydantic drops
  unknown keys) and disappears on the next config write.
- The sub-stream's resolution, frame rate and bitrate are tuned in the
  camera's own settings, not in minicam.
- Supersedes phase 4 of ADR 0001: no main/sub switching.

## Alternatives considered
- **Keep the per-camera choice, default to sub:** keeps a knob most setups
  would never touch, and a camera left on main still floods the uplink.
- **Two RTSP sessions (sub live, main for recordings):** best clip quality,
  but doubles camera connections, decoding/remux work and code paths on a Pi.
- **Automatic main/sub switching (ADR 0001 phase 4):** most complex; the
  savings are mostly the same as always using sub.

## Consequences
- Several times less bandwidth per viewer, on LAN and WAN, and less CPU on
  the Pi (smaller frames to decode, convert to RGB and JPEG-encode for the
  MJPEG fallback).
- Recordings are at sub-stream resolution: less detail for identifying faces
  or plates. Raising the sub-stream's resolution in the camera is the lever.
- Cameras whose sub-stream is disabled or lives at another path will not
  connect until their sub-stream is enabled.
