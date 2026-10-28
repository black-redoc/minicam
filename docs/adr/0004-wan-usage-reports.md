# 0004 Report WAN streaming usage to the central dashboard — Status: Accepted
Date: 2026-10-27

## Context
The central dashboard ([minicam-admin](https://github.com/black-redoc/minicam-admin)) needs WAN bytes
per subscriber and camera for the 15 GB monthly cap and the usage and cost views. Only the Pi sees
those bytes. The boundary was decided in minicam-admin ADR 0008
(`docs/adr/0008-cumulative-usage-reports.md`) and spec 012 (`docs/specs/012-usage-reports.md`); this
ADR records minicam's side only.

## Decision
- Central mode only (see [ADR 0003](0003-central-entitlements.md)); standalone installs count nothing.
- **What is counted:** the byte length of every binary WebSocket message sent to a WAN live-stream
  viewer (MJPEG frames, fMP4 init segments and fragments, audio included), per lowercased user email
  and camera. LAN streams, recording clips and JSON control messages are not counted. A user or camera
  the dashboard would reject (not an email; camera not 1-64 printable characters) is not counted, since
  one bad counter rejects a whole report.
- **Meter, flush, report** (`app/services/usage.py`): an in-memory `UsageMeter`; every 60 s the
  pending bytes are added to `usage_counters` (migration 0006) per Colombia month (fixed UTC-5) in one
  transaction and leave the meter only after the commit; every 5 min the counters of the current and
  previous month above `acked_bytes` are sent to `POST /api/device/usage` as cumulative values, in
  chunks of at most 1000 counters and 200 KiB, and `acked_bytes` is raised only on a 200.
- A random `reporter_id` (UUID, `usage_reporter`) is created once and kept, so a wiped SD card shows
  up as a new reporter instead of a counter that went down.
- Flushes are shielded from cancellation and serialized, and shutdown flushes once more before the
  database closes, so a restart neither loses nor double-counts the last minute.

## Consequences
- A crash loses at most the last minute of counted bytes: usage is undercounted, never overcounted.
- The SD card gets one write per active (user, camera) pair per minute, not one per frame.
- Usage reaches the dashboard within about 6 min; a long outage only delays it (counters are totals).
- Reports carry user emails, which the Pi already stores; the token and the counters are never logged.
