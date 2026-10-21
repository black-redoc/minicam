# Video Streaming

minicam streams live video to the browser using a **binary WebSocket** connection. Each message is a complete JPEG frame. Since the [session auth and camera-permission model](/doc/auth) was added, opening the WebSocket is a **two-step handshake**: first mint a short-lived stream token over HTTP, then open the WebSocket with that token.

## Step 1 — Issue a stream token

```
POST /api/stream/{camera_name}/token
```

Requires a valid session cookie, the `can_view` permission on `camera_name`, and — if the request arrives from outside the LAN — an active WAN access grant (see [Authentication & Authorization](/doc/auth)).

**Response `200`:**
```json
{ "token": "<jwt>", "expires_in": 30 }
```

The token is a separate, single-purpose JWT (distinct from the session cookie): it is bound to `camera_name` via a `camera` claim and expires after `expires_in` seconds (`MINICAM_STREAM_TOKEN_TTL_SECONDS`, default `30` — see [Configuration](/doc/configuration#security-settings)). It is *not* checked against the sessions table, so it keeps working — the same trust model as a signed URL — even if the session is revoked after the handshake succeeds.

**Response `401`:** no valid session. **Response `403`:** WAN access gate closed. **Response `404`:** no `can_view` permission on this camera (same status as "camera doesn't exist," so an unauthorized caller can't tell the two apart).

## Step 2 — Open the WebSocket

```
WS /ws/stream/{camera_name}?token=<jwt>
```

The `token` query parameter must be the value returned by step 1, issued for this exact `camera_name`.

## Behavior

- Each handler waits on the camera's `FrameBuffer` and is woken as soon as a new frame arrives (no polling).
- A frame is only sent once per viewer (no duplicate frames). A viewer slower than the camera skips the frames it missed and always gets the latest one.
- The connection is closed immediately with WebSocket close code **1008** (Policy Violation) if: the token is missing, malformed, expired, was issued for a different camera, or the requested camera is not registered.
- The connection stays open indefinitely as long as the camera is running. If the camera thread crashes, the WebSocket will stop delivering frames but the connection remains open.
- Since the token is only checked at handshake time, a client whose token expires mid-stream is **not** disconnected — the socket keeps delivering frames.

## Frame Format

Each WebSocket message is a **binary** payload containing a raw JPEG image. No framing, no metadata — just bytes you can feed directly to an `<img>` tag via a Blob URL.

## JavaScript Example

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

This is exactly the pattern implemented by `VideoStream` in `app/static/js/stream.js`, which also auto-reconnects (re-fetching a fresh token each time) on close or error.

## Performance Notes

| Parameter | Value |
|-----------|-------|
| Poll rate | 60 Hz |
| Frame format | JPEG (quality set in encoder) |
| Latency | ~1–2 frames (latest-frame buffer + WebSocket queue) |
| Protocol | Binary WebSocket over HTTP/1.1 upgrade |
| Stream token TTL | 30s by default (`MINICAM_STREAM_TOKEN_TTL_SECONDS`) |

## Multiple Viewers

Multiple browser tabs can connect to the same `WS /ws/stream/{camera_name}` simultaneously, each with its own stream token. RTSP demuxing/decoding and JPEG encoding are both shared: each frame is decoded once and encoded once per camera (`SharedJpegEncoder`, `app/services/shared_encoder.py`), with detection boxes drawn in that same single pass, and the resulting bytes are sent to every viewer. Extra viewers only add the cost of sending the bytes.

The dashboard closes a camera's WebSocket while its browser tab is hidden and reopens it when the tab becomes visible again, so background tabs use no server CPU or bandwidth.
