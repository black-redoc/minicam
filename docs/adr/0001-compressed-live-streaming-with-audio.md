# 0001 Compressed live streaming with on-demand audio — Status: Accepted
Date: 2026-10-23

## Context
- Live view is MJPEG over WebSocket: the server re-encodes every frame as
  JPEG and sends it to each viewer. Bandwidth grows per viewer and is far
  above the camera's own bitrate, so viewing through the Cloudflare Tunnel
  is slow (LAN is fine).
- Cameras already send compressed video: EZVIZ sends HEVC (even on
  `/h264/` RTSP paths), Hikvision sends H.264 or HEVC depending on its
  settings. Both send AAC audio when the microphone is on.
- A Raspberry Pi 5 has no hardware video encoder: transcoding is not an
  option. Whatever the camera sends is what can be delivered.
- minicam must support EZVIZ and Hikvision (and ideally any ONVIF/RTSP
  camera) with one code path: no per-vendor branches.

## Decision
1. **Codec-agnostic passthrough.** Live video is the camera's own packets,
   remuxed (never re-encoded) into fragmented MP4 and sent over the existing
   stream WebSocket (same two-step token handshake). The codec (H.264 or
   HEVC) and the presence and codec of audio are detected at runtime from
   the RTSP stream, never inferred from the vendor or the URL.
2. **Client picks the format.** The browser checks
   `MediaSource.isTypeSupported()` with the codec string the server
   announces. If it can play it, it uses fMP4 via Media Source Extensions;
   otherwise it falls back to today's MJPEG stream. The format is a query
   parameter of the WebSocket (`format=fmp4|mjpeg`).
3. **One muxer per camera, shared by its viewers**, like `SharedJpegEncoder`
   today. A new viewer receives the init segment and starts at the latest
   keyframe. A viewer that falls behind skips ahead to the next keyframe:
   compressed video can't skip individual frames. Fragments are emitted
   per frame, not per GOP, to keep latency low.
4. **Detection boxes are drawn by the client.** In fMP4 mode the server
   can't draw on the video, so it sends the detections as JSON text messages
   on the same WebSocket and the client draws them on a canvas over the
   `<video>`. MJPEG mode keeps drawing them server-side.
5. **Live audio on demand (off by default).**
   - Audio is never sent unless the viewer asks for it: the WebSocket takes
     `audio=1`, and turning audio on or off reconnects (a 1-2 s gap until the
     next keyframe).
   - Every time a camera view opens, audio starts off; the choice is not
     remembered.
   - New per-camera permission `can_listen`, set by admins next to
     `can_view`/`can_use_ptz` (default off). The stream token carries
     it, and `audio=1` without it is refused.
   - Only AAC is passed through; with no audio or another codec (e.g. G.711)
     the speaker button is hidden. The MJPEG fallback has no audio.
6. **Audio in event recordings (admin opt-in, off by default).**
   - New setting `recordings.include_audio = false`.
   - In the admin "Event recordings" card, ticking its checkbox opens a
     dialog with a legal notice and **Accept / Cancel**. Only Accept turns
     it on; Cancel leaves it unticked. Unticking needs no dialog.
   - The dialog text is a first step only; its content will be expanded
     later.
   - Users without `can_listen` on the camera never hear a clip's audio:
     they get the clip remuxed on the fly without its audio track (no
     re-encoding). Users with it get the file as stored.

## Phases (one PR each)
1. fMP4 live video + MJPEG fallback + client-side detection boxes.
2. Live audio on demand + `can_listen` (migration, admin UI, token claim).
3. Audio in recordings + admin setting with the legal-notice dialog.
4. ~~Later, separate ADR: automatic main/sub stream switching on slow links
   (needs a second RTSP session per camera).~~ Superseded by ADR 0002: the
   sub-stream is always used.

## Alternatives considered
- **Lower MJPEG quality/FPS on WAN (adaptive MJPEG):** small change, but it
  stays several times heavier than the camera's own stream and has no audio.
  It is kept as a possible quick win, not as the solution.
- **WebRTC** (e.g. via go2rtc/MediaMTX): lowest latency, but it adds a
  separate service and dependency. Browser HEVC support over WebRTC is
  also weaker than over MSE.
- **HLS / LL-HLS:** standard and cacheable, but adds seconds of latency
  and segment files on the SD card.
- **Transcoding to one codec:** not feasible on a Pi 5 without a hardware
  encoder.

## Consequences
- **Browser support.** Safari plays HEVC; Chrome and Edge play it with
  hardware decoding. Firefox and some Chromium forks will usually get
  MJPEG for HEVC cameras. H.264 cameras play everywhere.
- **Mobile app.** minicam-mobile needs its own fMP4 player to benefit;
  until then it keeps using MJPEG, which stays supported.
- **Server CPU.** Decoding for detection stays as it is; live viewing via
  fMP4 costs almost no CPU because nothing is re-encoded.
- **Privacy.** Audio carries conversations, so it is opt-in at every
  level: the `can_listen` permission, the per-view toggle, and the admin
  setting for recordings.
