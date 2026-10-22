# Detection Notifications

minicam can notify users when [object detection](/doc/detection) sees one of a camera's selected classes (e.g. `person`). Notifications are off by default.

## What triggers a notification

- A camera notifies on **its detection classes**: the classes checked under **Object detection** in its camera settings. There is no separate class list for notifications, and a camera with no detection classes (or a device with no detection backend) never notifies.
- Detections must appear in **2 consecutive inference results** before a notification fires. This filters out one-frame false positives.
- Each camera then waits out a **cooldown** (60 s by default) before it can notify again, however long the object stays in view. Cameras have independent cooldowns.

## Turning it on

Notifications are configured in two layers, like detection itself:

1. **All cameras (Admin page).** The **Detection notifications** card switches notifications on or off for every camera and sets the per-camera cooldown. It is saved in the `[notifications]` section of `config.toml`.
2. **One camera (Camera settings).** **Detection notifications** in a camera's settings overrides the global switch for that camera:
   - **Use global setting** (`inherit`, the default) follows the Admin switch.
   - **On for this camera** (`on`) notifies even while the global switch is off.
   - **Off for this camera** (`off`) never notifies, even while the global switch is on.

Changes apply immediately, without restarting detection.

```toml
[notifications]
enabled = true
cooldown_seconds = 60

[[cameras]]
name = "entrance"
host = "192.168.1.100"
detection_classes = ["person"]
notifications = "on"   # "inherit" | "on" | "off"
```

## Who receives them

A notification goes only to active users with `can_view` on that camera (see [Authentication & Authorization](/doc/auth)). Recipients are resolved when each notification is sent, so a revoked permission stops notifications right away. Admins need a `can_view` permission too, like for the dashboard. The WAN access flag (`wan_access`) is not required to receive notifications, including Web Push. It only gates WAN requests, such as opening the event stream or registering a browser from outside the LAN.

## How they are delivered

- **In the dashboard (Server-Sent Events).** Every open dashboard tab keeps an `EventSource` on `GET /api/notifications/events` and shows a toast for each detection. This works on the LAN over plain HTTP as well. The server closes the stream every 10 minutes and the browser reconnects, so the session and the WAN gate are checked again.
- **On the device, even with the tab closed (Web Push).** Each user turns this on per browser from the dashboard's **⋮ → Notifications on this device** menu item. The browser asks for permission, registers the service worker (`/service-worker.js`) and sends its push subscription to minicam. From then on, minicam sends each notification to that browser's push service (Google FCM, Mozilla, Apple or Microsoft), and the browser shows it as a system notification. Clicking it opens the dashboard.

Browsers only allow Web Push in a secure context: HTTPS (e.g. through the [Cloudflare Tunnel](/doc/cloudflare)) or `http://localhost`. On plain-HTTP LAN access the menu item is disabled, and only the in-page toasts work. A subscription belongs to the origin it was created on, so enable it from the address you normally use (usually the tunnel hostname).

### Web Push details

- Payloads are end-to-end encrypted for the browser (RFC 8291, `aes128gcm`), so the push service can't read them. They contain the camera name, the detected classes and the time, never an image.
- minicam signs each push with its own VAPID key (RFC 8292). The key is generated on first start and stored in the SQLite database. Deleting it invalidates every existing subscription.
- `MINICAM_VAPID_SUBJECT` sets the VAPID contact (`mailto:` or `https:` URL) that push services may use to reach the device owner. It defaults to `mailto:minicam@example.com`. Set your own address, because some push services reject placeholder contacts.
- Only endpoints on the known browser push services are accepted. minicam never sends requests to arbitrary URLs supplied by a browser.
- Subscriptions the push service reports as gone (`404`/`410`) are deleted automatically.

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/notifications/settings` | admin | Global settings (`enabled`, `cooldown_seconds`) |
| `PUT` | `/api/notifications/settings` | admin | Save global settings |
| `GET` | `/api/notifications/events` | session + WAN gate | SSE feed of `detection` events for viewable cameras |
| `GET` | `/api/notifications/push/public-key` | session | VAPID `applicationServerKey` |
| `POST` | `/api/notifications/push/subscriptions` | session + WAN gate | Register this browser's `PushSubscription` |
| `DELETE` | `/api/notifications/push/subscriptions` | session | Unregister a subscription (`{"endpoint": ...}`) |
| `GET` | `/service-worker.js` | none | Service worker that displays pushes |

Every SSE `detection` event and every push carries the same JSON:

```json
{
  "type": "detection",
  "camera": "entrance",
  "classes": ["person"],
  "detected_at": "2026-10-21T12:00:00+00:00",
  "title": "minicam",
  "body": "person detected on entrance"
}
```

## Not included yet

Per-camera alarm schedules (e.g. only notify outside business hours), and native APNs/FCM delivery for the future mobile app, will build on this same notification service.
