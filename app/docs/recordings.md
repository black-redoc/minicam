# Event Recordings

minicam can save a short video clip each time a camera's [object detection](/doc/detection) sees one of its selected classes (e.g. `person`). Recording is off by default.

## What triggers a recording

- A camera records on **its detection classes**: the classes checked under **Object detection** in its camera settings. A camera with no detection classes, or a device with no detection backend, never records.
- Detections must appear in **2 consecutive inference results** before a clip starts, the same false-positive filter used by [notifications](/doc/notifications).
- Each clip starts about **3 seconds before** the first detection, using a small in-memory buffer, and stops about **5 seconds after** the last one. A person who walks out of view for a moment and comes back stays in the same clip.
- A clip is split every **5 minutes**, on a keyframe, so an event that lasts longer becomes several consecutive clips with nothing lost between them.

## Turning it on

The **Event recordings** card on the Admin page has the global switch and the **storage limit**: 1, 5, 10 or 50 GB. It applies to every camera, and changes apply immediately without restarting anything.

A limit is only selectable if the disk can hold it: the free space plus the space recordings already use, minus 1 GB that is always kept free for the system. Limits that don't fit are shown struck through with **Not enough space** next to them. The API refuses them too.

```toml
[recordings]
enabled = true
max_storage_gb = 10   # 1 | 5 | 10 | 50
```

## Storage and retention

- Clips are saved as MP4 files in `app/data/recordings/`. Their list (camera, start time, duration, size, detected classes) is stored in the SQLite database.
- When the clips together exceed the storage limit, or less than 1 GB is left free on disk, the **oldest clips are deleted first**. Lowering the limit deletes the excess right away.
- If the disk is still nearly full after that (e.g. something else filled it), new clips are skipped and a warning is logged at most once a minute.
- A clip is written as `<name>.mp4.part` and renamed only once complete. On startup, minicam deletes leftover `.part` files (e.g. from a power cut) and any clip file missing from the database.

## Watching recordings

Open **⋮ → Recordings** on the dashboard. The page lists the clips of every camera you can view, newest first and grouped by day, with their detected classes. Click one to play it, or **Download** it as an MP4. Admins can also **Delete** a clip.

Who can see a clip follows the same rules as live view: you need `can_view` on its camera (see [Authentication & Authorization](/doc/auth)). From outside the LAN, the WAN access gate applies, as for live streams. A clip whose camera was deleted or renamed is only visible to users who still have `can_view` on that camera name, and it is deleted by retention like any other.

## How it works (and what it costs)

- Clips are **not re-encoded**. minicam copies the camera's own video packets into the MP4 file (a remux), so recording costs almost no CPU and keeps the camera's full quality. A Raspberry Pi 5 has no hardware video encoder, so re-encoding would compete with live streaming and detection for the CPU.
- As a consequence, clips **don't show the detection boxes** that the live view draws.
- Clips keep the camera's codec, **H.264 or HEVC (H.265)**, whatever the RTSP URL says: many Hikvision and EZVIZ cameras send HEVC on `/h264/...` paths. HEVC clips play in Safari and in Chrome or Edge on devices with hardware HEVC decoding; other browsers may not play them, but **Download** still works with any player (for example VLC).
- The pre-roll buffer holds the last few seconds of compressed video per camera, starting at a keyframe. That is a few MB of RAM per camera, and only while recording is on.
- Each clip is written by its own thread, so a slow SD card never stalls the live stream. If the disk falls more than about 80 seconds of video behind, that clip is abandoned and logged.

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/recordings` | session | Recordings page |
| `GET` | `/api/recordings/settings` | admin | Settings, disk usage and which limits fit |
| `PUT` | `/api/recordings/settings` | admin | Save `{enabled, max_storage_gb}` |
| `GET` | `/api/recordings?camera=&before=&limit=` | session + WAN gate | Clips of viewable cameras, newest first |
| `GET` | `/api/recordings/{id}/video[?download=true]` | session + `can_view` + WAN gate | The MP4 clip (supports `Range`) |
| `DELETE` | `/api/recordings/{id}` | admin | Delete a clip and its file |

`GET /api/recordings/settings` returns:

```json
{
  "enabled": true,
  "max_storage_gb": 10,
  "used_bytes": 734003200,
  "free_bytes": 21474836480,
  "options": [
    {"gb": 1, "available": true},
    {"gb": 5, "available": true},
    {"gb": 10, "available": true},
    {"gb": 50, "available": false}
  ]
}
```

`GET /api/recordings` returns up to `limit` clips (default 50, max 200). If there may be more, pass `next_before` as `before` to get the next page:

```json
{
  "items": [
    {
      "id": 42,
      "camera_name": "entrance",
      "started_at": "2026-10-22T14:03:11.250000+00:00",
      "duration_seconds": 18.4,
      "size_bytes": 5242880,
      "classes": ["person"]
    }
  ],
  "next_before": null
}
```

A clip on a camera you can't view returns `404`, as if it didn't exist.
