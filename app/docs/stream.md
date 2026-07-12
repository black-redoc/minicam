# Video Streaming

minicam streams live video to the browser using a **binary WebSocket** connection. Each message is a complete JPEG frame.

## Endpoint

```
WS /ws/stream/{camera_name}
```

## Behavior

- The server polls the `DoubleFrameBuffer` at **60 Hz**.
- A frame is only sent when it has changed since the last send (no duplicate frames).
- If the requested camera does not exist, the connection is closed immediately with WebSocket close code **1008** (Policy Violation).
- The connection stays open indefinitely as long as the camera is running. If the camera thread crashes, the WebSocket will stop delivering frames but the connection remains open.

## Frame Format

Each WebSocket message is a **binary** payload containing a raw JPEG image. No framing, no metadata — just bytes you can feed directly to an `<img>` tag via a Blob URL.

## JavaScript Example

```js
const ws = new WebSocket(`ws://${location.host}/ws/stream/entrada`);
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

## Performance Notes

| Parameter | Value |
|-----------|-------|
| Poll rate | 60 Hz |
| Frame format | JPEG (quality set in encoder) |
| Latency | ~1–3 frames (double buffer + WebSocket queue) |
| Protocol | Binary WebSocket over HTTP/1.1 upgrade |

## Multiple Viewers

Multiple browser tabs can connect to the same `WS /ws/stream/{camera_name}` simultaneously. Each WebSocket handler reads independently from the `DoubleFrameBuffer` — RTSP demuxing/decoding is shared and does not cost extra CPU per viewer. JPEG encoding, however, is **not** shared: each connected WebSocket encodes its own copy of every new frame via `encode_jpeg` (`app/services/encoder.py`), so CPU cost for encoding scales with the number of active viewers.
