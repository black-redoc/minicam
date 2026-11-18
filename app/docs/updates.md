# Software Updates

A device installed from the minicam Raspberry Pi image can update itself to a newer minicam release. An admin starts the update from the minicam mobile app (**Settings → minicam on this device**); the device downloads the release, checks its signature, installs it and restarts minicam. Nothing updates on its own: the admin chooses when the device restarts.

## What happens

1. minicam asks GitHub for the latest release of `black-redoc/minicam` (at most every 6 hours, or when an admin refreshes). A release is offered only if it is newer than the installed one and its update bundle is published.
2. When an admin asks for it, a separate root helper (`minicam-update`) downloads `minicam-vX.Y.Z-linux-arm64.tar.gz` and its signature from that release.
3. The bundle must be signed with minicam's release key (minisign). The device carries the public key; the signature also names the version, so an older bundle can't be passed off as a newer one. Older versions are never installed.
4. minicam stops, the database is backed up, the code is replaced and minicam starts again. Your settings, users, cameras, models and recordings stay as they are.
5. If the new minicam does not answer within 3 minutes, the previous version and the database backup come back.

Live views and recording stop for about a minute while minicam restarts. The download is about 200 MB.

Devices flashed with an image older than the first release that includes updates have no update helper: flash a new image once.

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/system/update[?refresh=true]` | admin | Installed and latest version, and the progress of an update |
| `POST` | `/api/system/update` | admin | Start the update to `{version}`, which must be the offered release |

`GET /api/system/update` returns:

```json
{
  "available": true,
  "current": "v0.6.0",
  "latest": "v0.7.0",
  "update_available": true,
  "state": "downloading",
  "version": "v0.7.0",
  "message": "Downloading minicam v0.7.0",
  "checked_at": "2026-11-18T12:00:00+00:00"
}
```

`available` is `false` on an installation without the update helper (for example a development checkout). `state` is one of `idle`, `requested`, `downloading`, `verifying`, `installing`, `restarting`, `done` or `error` (with `message` saying why). While minicam restarts the API does not answer for a moment; ask again until `current` is the new version.

`POST` answers `409` when there is no such update or one is already running.
