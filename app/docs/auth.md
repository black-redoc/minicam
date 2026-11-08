# Authentication & Authorization

minicam gates every camera behind a session-authenticated, per-camera permission model, plus a separate gate for access from outside the local network (WAN). This page covers the `/api/auth/*` endpoints and the underlying session/permission/WAN-gate design used by every other router.

## Sessions

Login is email + password. A successful login opens a **revocable** session and sets an `httponly` cookie (`minicam_session`).

```
POST /api/auth/login
```

**Request body:**
```json
{ "email": "admin@example.com", "password": "secret" }
```

**Response `200`:** the logged-in user.
```json
{ "id": 1, "email": "admin@example.com", "is_admin": true }
```

**Response `401`** (wrong email/password, or the account is deactivated):
```json
{ "detail": "Invalid email or password" }
```

Under the hood, the session is a signed JWT (`app.services.auth.TokenService`) whose `jti` claim is also stored as a row in the `sessions` table (`app/db/migrations/0001_initial.sql`). Every authenticated request re-checks that row, so revoking a session (logout, or an admin deactivating the account) takes effect immediately — not just when the JWT itself expires.

| Property | Value |
|----------|-------|
| Cookie name | `minicam_session` |
| Cookie flags | `httponly`, `samesite=lax`, **not** `secure` (deliberate — minicam is also served over plain HTTP on the LAN) |
| Default TTL | 24h (`MINICAM_SESSION_TTL_SECONDS`, see [Configuration](/doc/configuration#security-settings)) |
| Signing | HS256, secret from `MINICAM_JWT_SECRET` |

```
POST /api/auth/logout
```

Revokes the current session (if any) and clears the cookie. Idempotent — safe to call with no session.

```
GET /api/auth/me
```

Returns the current user. **`401`** if there is no valid session.

## Per-Camera Permissions

Access to a specific camera is **deny-by-default**: a user can view or PTZ-control a camera only if an explicit row exists in `camera_permissions` granting it. There is no "all cameras" or role-based shortcut — every camera needs its own grant.

| Permission | Governs |
|------------|---------|
| `can_view` | Seeing the camera on the dashboard, and minting a stream token (`POST /api/stream/{camera_name}/token`) |
| `can_use_ptz` | Sending PTZ commands (`POST /api/ptz/{camera_name}/{action}`) |
| `can_listen` | Hearing the camera's microphone in the live view (a stream token with `audio=true`). Only takes effect together with `can_view` |

`can_use_ptz` does **not** imply `can_view` or vice versa — they're independent flags on the same row. `can_listen` is off for every grant unless an admin turns it on (the **Listen** column on `/admin`): audio can carry conversations, so it is never implied by `can_view`.

When a user lacks a permission, the response is **`404`**, not `403` — a user without access must not be able to tell whether the camera exists at all.

### Managing permissions (admin-only)

```
POST /api/auth/permissions
```

**Request body:**
```json
{ "user_id": 2, "camera_name": "entrada", "can_view": true, "can_use_ptz": false, "can_listen": false }
```

Upserts the permission row for that `(user_id, camera_name)` pair.

**Response `200`:**
```json
{ "user_id": 2, "camera_name": "entrada", "can_view": true, "can_use_ptz": false, "can_listen": false }
```

```
GET /api/auth/permissions?user_id=2
```

Lists every camera permission granted to that user.

### Listing your own cameras

```
GET /api/me/cameras
```

Any signed-in user. Returns the cameras they can view, in config order, with whether they can also use PTZ and hear its audio — the JSON form of the dashboard's camera list, for native clients.

```json
[{ "name": "entrada", "can_use_ptz": true, "can_listen": false }]
```

## Managing Users (admin-only)

There is no self-service signup — every account is created by an existing admin. The only exception is the very first account, created through first-run setup (see [Bootstrapping](#bootstrapping)).

```
POST /api/auth/users
```

**Request body:**
```json
{ "email": "viewer@example.com", "password": "at-least-8-chars", "is_admin": false }
```

**Response `201`:** the created user (never includes `password_hash`). **Response `409`** if the email is already registered.

```
GET /api/auth/users
```

Lists every account.

```
DELETE /api/auth/users/{user_id}
```

**Response `204`** on success. **`400`** if you try to delete your own account. **`404`** if the user doesn't exist. **`409`** if the target is the last remaining admin — minicam refuses to lock itself out.

## WAN Access Gate

Separate from per-camera permissions, minicam classifies every request as **LAN** or **WAN** (`app/services/network.py`) and, for any request classified as WAN, additionally requires the user to have an active `wan_access` grant before it can view a stream or send a PTZ command. LAN requests never hit this check.

Classification is fail-safe: any request that can't be confidently placed on the LAN — unparsable/missing client IP, or a request forwarded through a configured `MINICAM_TRUSTED_PROXIES` proxy — is classified as **WAN**.

`wan_access` is deny-by-default too: no row for a user means WAN access is off. If the gate is closed, WAN callers get **`403`** ("WAN access is not enabled for this account") from `POST /api/stream/{camera_name}/token` and `POST /api/ptz/{camera_name}/{action}`.

### Granting WAN access (admin-only)

Tick **WAN access** on the user's card in the [Admin Page](#admin-page). It takes effect on that user's next request. The same is available over the API:

```
GET  /api/auth/wan-access?user_id=2
POST /api/auth/wan-access   { "user_id": 2, "is_active": true }
```

Both return `{ "user_id": 2, "is_active": true }`; a user with no row reads as `false`. `POST` returns **`404`** if the user doesn't exist.

Admins are gated too: an admin who wants to use minicam from outside needs their own WAN access ticked (do it from the LAN first).

### Central mode

With the device paired with the central dashboard, the dashboard owns WAN access instead. Pair it from minicam-mobile (Admin → Central dashboard, admins on the LAN): in the dashboard's `/sites`, **Pair device** shows a one-time code; the app sends minicam the dashboard address and the code (`POST /api/system/central`), and minicam exchanges the code with the dashboard for its device token, stores it in `central.json` (0600) and starts syncing without a restart. Pairing again rotates the token; `DELETE /api/system/central` disconnects. On manual installs `MINICAM_CENTRAL_URL` and `MINICAM_DEVICE_TOKEN` in `.env` (see [Configuration](/doc/configuration)) do the same and take precedence: the app cannot change them. In central mode: every 60 s minicam fetches the site's entitlements and sets each user's `wan_access` from them (matched by login email, case-insensitively; anyone not listed is off). `POST /api/auth/wan-access` then answers **`409`** and the Admin page checkbox shows that message. If the dashboard cannot be reached, the last answer keeps working for up to 30 minutes; after that every WAN request and open WAN stream is denied until a sync succeeds. LAN access never depends on it.

## Admin Page

`GET /admin` (admin-only) serves the user + permission management UI (`app/templates/admin.html`, `app/static/js/admin.js`): create users, list users, delete users, grant/revoke per-camera permissions, and grant/revoke each user's WAN access. Camera CRUD itself (add/edit/remove camera, WS-Discovery) stays on the main dashboard's "Camera settings" modal — see [Multi-Camera Setup](/doc/cameras).

## Bootstrapping

While the `users` table is empty, `/login` shows a **Create admin account** form instead of the sign-in form. Submitting it creates the first account as an admin and signs it in; from then on `/login` only ever shows the normal sign-in form.

```
GET  /api/auth/setup        -> { "setup_required": true | false, "claim": "none" | "led",
                                 "window_open": true | false, "window_seconds_left": 1799 }
POST /api/auth/setup/claim  -> { "digits": 4, "expires_in": 300 }   (LED claim only)
POST /api/auth/setup        { "email": "...", "password": "...", "setup_code": "2468" }
                            (password min. 8 chars; setup_code only with the LED claim)
```

- `201` — admin created, session cookie set (same cookie as `/api/auth/login`).
- `409` — any account already exists. Setup can succeed only once: the emptiness check and the insert are a single atomic SQL statement (`SQLiteUserRepository.create_first_admin`), so two concurrent requests can't both win.
- `403` — the request was classified as WAN. First-run setup is LAN-only so an unconfigured device can't be claimed by whoever reaches it first through the Cloudflare tunnel.

### LED claim (Raspberry Pi OS image)

With `MINICAM_SETUP_CLAIM=led` (set on first boot by the image) being on the LAN is not enough: whoever creates the admin must also be able to see the device. The device advertises itself on the LAN as `_minicam._tcp` (mDNS/DNS-SD, so the apps find it without an IP), and:

- Setup is open for **30 minutes after minicam starts**, and only while no user exists; restarting the device (power cycle) opens it again. `window_open` is always `false` for WAN requests.
- `POST /api/auth/setup/claim` (LAN) makes the green ACT LED blink a 4-digit code, each digit 1–9: N short blinks per digit, a longer pause between digits and an even longer one before it repeats. The code is valid for 5 minutes and never appears in a response or a log. At most one new code every 30 s (`429`) and 5 per window (`403`); `503` if the LED can't be controlled.
- `POST /api/auth/setup` then needs `setup_code`. A missing, expired or wrong code is `403`; after 3 wrong codes the code is spent and a new one must be requested. On success the LED goes back to its usual activity.

The LED is written through `/sys/class/leds/ACT/{trigger,brightness}` (`MINICAM_SETUP_LED_PATH` to change the LED); the image's udev rule gives the `minicam` group write access to only those two files.

The new admin starts with **no camera permissions** (camera access is deny-by-default for admins too), so the next step is granting yourself **View** and **PTZ** in `/admin` — see [Grant yourself camera permissions](/doc/configuration#grant-yourself-camera-permissions).

The CLI remains available as an alternative (for example on a headless install). See [Configuration](/doc/configuration#bootstrapping-the-first-admin).
