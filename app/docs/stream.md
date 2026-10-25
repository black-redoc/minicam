# Video Streaming

minicam streams live video to the browser over a **WebSocket**, in one of two formats:

- **fMP4**: the camera's own compressed video (H.264 or HEVC), remuxed without re-encoding into fragmented MP4 and played by the browser through Media Source Extensions. It uses a small fraction of the bandwidth of JPEG frames (for example ~150 kbit/s instead of ~5–8 Mbit/s for a 768x432 sub stream), which is what makes viewing through the Cloudflare Tunnel smooth.
- **MJPEG**: one complete JPEG per message. The fallback for browsers that can't play the camera's codec, and the default for clients that don't ask for fMP4 (such as the mobile app today).

The dashboard picks fMP4 automatically when `MediaSource.isTypeSupported()` accepts the camera's codec, and MJPEG otherwise. HEVC plays in Safari (including iPhone, through `ManagedMediaSource`) and in Chrome, Edge and Brave on devices with hardware HEVC decoding; Firefox usually gets MJPEG for HEVC cameras. H.264 cameras play as fMP4 everywhere.

The FPS counter shows which format each view is using, e.g. `11 FPS · HEVC` (fMP4) or `11 FPS · MJPEG`. Hover it for the exact codec or the reason for MJPEG; the browser console logs the same (`[minicam] office: HEVC (...)`). Since the [session auth and camera-permission model](/doc/auth) was added, opening the WebSocket is a **two-step handshake**: first mint a short-lived stream token over HTTP, then open the WebSocket with that token.

## Step 1 — Issue a stream token

```
POST /api/stream/{camera_name}/token
```

Requires a valid session cookie, the `can_view` permission on `camera_name`, and — if the request arrives from outside the LAN — an active WAN access grant (see [Authentication & Authorization](/doc/auth)).

**Response `200`:**
```json
{ "token": "<jwt>", "expires_in": 30, "video_mime": "video/mp4; codecs=\"hvc1.1.6.L90\"", "audio_mime": null }
```

`video_mime` is the MIME type, with its RFC 6381 `codecs`, of the camera's live video, detected from the RTSP stream itself (never from the vendor or the URL: many cameras send HEVC on `/h264/...` paths). It is `null` until the camera has connected and sent a keyframe; the client then uses MJPEG.

`audio_mime` is the same stream with the camera's audio, e.g. `video/mp4; codecs="hvc1.1.6.L90,mp4a.40.2"`. It is `null` unless the camera sends AAC audio (other audio codecs can't be passed through) **and** the caller has the `can_listen` permission. To hear it, request the token with `?audio=true`: the token then also authorizes audio, and the request fails with **`403`** without `can_listen`.

The token is a separate, single-purpose JWT (distinct from the session cookie): it is bound to `camera_name` via a `camera` claim and expires after `expires_in` seconds (`MINICAM_STREAM_TOKEN_TTL_SECONDS`, default `30` — see [Configuration](/doc/configuration#security-settings)). It is **single-use**: it opens one WebSocket, and a second handshake with the same token is rejected (`1008`) even before it expires, so a token that leaks into a log or browser history is useless. Every new connection, reconnects included, mints a fresh token.

**Response `401`:** no valid session. **Response `403`:** WAN access gate closed. **Response `404`:** no `can_view` permission on this camera (same status as "camera doesn't exist," so an unauthorized caller can't tell the two apart).

## Step 2 — Open the WebSocket

```
WS /ws/stream/{camera_name}?token=<jwt>
```

The `token` query parameter must be the value returned by step 1, issued for this exact `camera_name`. Add `&format=fmp4` for the camera's own video (see [fMP4 format](#fmp4-format)); without it the stream is MJPEG. Add `&audio=true` as well for its audio; the socket is closed with **1008** unless the token was issued with `audio=true`, and MJPEG never carries audio.

## Behavior

- Each handler waits on the camera's `FrameBuffer` and is woken as soon as a new frame arrives (no polling).
- A frame is only sent once per viewer (no duplicate frames). A viewer slower than the camera skips the frames it missed and always gets the latest one.
- The connection is closed immediately with WebSocket close code **1008** (Policy Violation) if: the token is missing, malformed, expired, already used, was issued for a different camera, or the requested camera is not registered.
- The token is tied to the session it was issued from. While the stream is open, minicam re-checks every 10 seconds that this session is still valid (not logged out, revoked or expired), the user is still active, they still have `can_view` (and `can_listen` for audio) and, off-LAN, WAN access is still enabled. Once any of these stops being true, the connection is closed with **1008**.
- Otherwise the connection stays open indefinitely as long as the camera is running. If the camera thread crashes, the WebSocket will stop delivering frames but the connection remains open.
- Since the token is only checked at handshake time, a client whose token expires mid-stream is **not** disconnected — the socket keeps delivering frames.

## fMP4 format

With `format=fmp4` the socket carries, in order:

1. A **text** message `{"type": "init", "mime": "<video_mime>"}`, then a **binary** message with the MP4 init segment (`ftyp` + `moov`). Both are sent again whenever the camera reconnects; the client must then start a new `MediaSource`.
2. One **binary** message per frame: an MP4 fragment (`moof` + `mdat`), and with `audio=true` also the audio between frames. A new viewer first receives the fragments since the latest keyframe, so playback starts immediately.
3. **Text** messages `{"type": "detections", "width", "height", "boxes": [{"label", "confidence", "color", "x1", "y1", "x2", "y2"}]}` whenever the camera's detections change. Coordinates are in frame pixels (`width` x `height`); the client draws the boxes over the video, since they can't be drawn into compressed video.

The video is never re-encoded, so it costs the server almost no CPU. Without `audio=true` no audio is ever sent: the server keeps a separate video-only stream for those viewers.

## Live audio

The dashboard shows a speaker button on a camera's single view when its audio can be heard (`audio_mime` set and playable by the browser). Audio is **off every time a camera is opened**; the button reconnects with `audio=true` (a 1–2 s gap while the stream restarts at a keyframe) and the FPS indicator then reads e.g. `11 FPS · HEVC + audio`. Admins grant listening per user and camera with **Listen** on `/admin`.

Many cameras (EZVIZ among them) timestamp audio and video on unrelated clocks and round audio timestamps. minicam measures the offset between the two clocks from packet arrival times during the first second of each connection (audio is held back until then), and restamps audio frames back to back, so audio stays in sync with the video whatever the camera vendor. One remuxer per camera is shared by all its viewers, and it only runs while someone watches (plus a few seconds after the last viewer leaves): the first viewer after a pause starts at the camera's next keyframe, usually within 2–4 s. A viewer that falls more than a few seconds behind skips ahead to the next keyframe. The connection closes with code **1001** if the camera is removed or renamed, and with **1008** if `format=fmp4` is requested for a camera that has no live feed.

## Frame Format (MJPEG)

Each WebSocket message is a **binary** payload containing a raw JPEG image. No framing, no metadata — just bytes you can feed directly to an `<img>` tag via a Blob URL.

## JavaScript Example (MJPEG)

```js
async function fetchStreamToken(cameraName) {
  const res = await fetch(`/api/stream/${encodeURIComponent(cameraName)}/token`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to obtain stream token: ${res.status}`);
  const { token } = await res.json();
  return token;
}

const cameraName = "entrada";
const token = await fetchStreamToken(cameraName);

const proto = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(
  `${proto}//${location.host}/ws/stream/${encodeURIComponent(cameraName)}`
  + `?token=${encodeURIComponent(token)}`
);
ws.binaryType = "blob";

const img = document.getElementById("camera-feed");

ws.onmessage = (event) => {
  const prev = img.src;
  img.src = URL.createObjectURL(event.data);
  // revoke previous blob to avoid memory leak
  if (prev.startsWith("blob:")) URL.revokeObjectURL(prev);
};

ws.onclose = (event) => {
  console.warn("Stream closed, code:", event.code);
};
```

This is the MJPEG path of `VideoStream` in `app/static/js/stream.js`, which also auto-reconnects (re-fetching a fresh token each time) on close or error. Its fMP4 path (`Fmp4Player`) feeds the binary messages to a `SourceBuffer`, keeps playback near the newest frame, and reconnects as MJPEG if the browser fails to play the stream.

## Performance Notes

| Parameter | Value |
|-----------|-------|
| Poll rate | 60 Hz |
| Frame format | fMP4 (camera's own H.264/HEVC) or JPEG (quality 85 on LAN; 70 and at most 10 FPS off-LAN) |
| Latency | ~1–2 frames (latest-frame buffer + WebSocket queue) |
| Protocol | Binary WebSocket over HTTP/1.1 upgrade |
| Stream token TTL | 30s by default (`MINICAM_STREAM_TOKEN_TTL_SECONDS`) |

## Multiple Viewers

Multiple browser tabs can connect to the same `WS /ws/stream/{camera_name}` simultaneously, each with its own stream token. RTSP demuxing/decoding and JPEG encoding are both shared: each frame is decoded once and encoded once per camera (`SharedJpegEncoder`, `app/services/shared_encoder.py`), with detection boxes drawn in that same single pass, and the resulting bytes are sent to every viewer. Extra viewers only add the cost of sending the bytes.

MJPEG viewers outside the LAN (for example through the Cloudflare Tunnel) get lighter JPEGs, at a lower quality and capped at 10 FPS, to spare the home uplink; frames that arrive in between are skipped. LAN and off-LAN viewers each share their own single encode per frame.

The dashboard closes a camera's WebSocket while its browser tab is hidden and reopens it when the tab becomes visible again, so background tabs use no server CPU or bandwidth.
