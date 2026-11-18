# 0012 Detection boxes in recordings as a sidecar track — Status: Accepted
Date: 2026-11-18

## Context
Live viewers draw the YOLO boxes over the video (`{"type": "detections"}` messages), but event
recordings only keep the camera's packets (a remux, no re-encoding), so a recorded clip shows no
boxes. A Raspberry Pi 5 has no hardware video encoder: drawing the boxes into the clip would mean
decoding and re-encoding every clip on the CPU, competing with live streaming and detection.

## Decision
- While recording is on, the recording service keeps each camera's recent detection snapshots
  (`time.monotonic()` of the analysed frame, frame size, boxes) for as long as the longest clip.
- A clip remembers the monotonic time of its first packet. When it is registered, the snapshots
  inside the clip are written next to it as `<clip>.detections.json`:
  `{version: 1, width, height, frames: [{t, boxes: [{label, confidence, color, x1, y1, x2, y2}]}]}`,
  `t` in seconds from the start of the clip, boxes in frame pixels like the live messages. A frame
  with no boxes clears them.
- `GET /api/recordings/{id}/detections` (session + `can_view` + WAN gate, 404 like the video)
  serves it; a clip with no sidecar (older clips) answers an empty track.
- The sidecar is deleted with its clip (API delete, retention, startup cleanup of orphans).
- Clients draw the boxes over the player at the playback position, like live.

## Alternatives considered
- **Burn the boxes into the MP4**: plays everywhere, but needs a CPU re-encode per clip on a Pi.
- **Store the track in SQLite**: one more table and migration for data that lives and dies with
  a file on disk; a sidecar file keeps clip and track together.
- **WebVTT/MP4 metadata track**: no player on the web or the phones draws boxes from it.

## Consequences
- Recording stays a remux; the extra cost is a small JSON file per clip.
- Box timing is the analysis time of the frame, so boxes may lag the picture by up to one
  detection interval, as in the live view.
- Downloaded clips have no boxes.
