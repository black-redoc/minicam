# 0011 Detection pushes to the mobile apps through the central relay — Status: Accepted
Date: 2026-11-09

## Context
minicam sends detection notifications over SSE and Web Push. The native apps need system
notifications with the app closed: Android through FCM (minicam-mobile ADR 0007). The Firebase key
must not sit on the Pi, so minicam-admin relays the messages (minicam-admin ADR 0013,
`/api/device/push-tokens` and `/api/device/push`, device token auth). minicam stays the authority
on who receives what, as for Web Push.

## Decision
- **Routes** (next to Web Push): `POST /api/notifications/mobile/tokens` (session + WAN gate)
  `{platform: "android", token, key}` stores the phone's FCM token and its AES-256 key for the
  caller and registers the token at the relay first (relay failure: `502`, nothing stored). `key`
  is 32 bytes base64url, `token` 1-4096 printable ASCII; a token moves to the latest user that
  registers it. `DELETE` (session) drops it here and at the relay. `GET
  /api/notifications/mobile/status` (session) answers `{available}`: paired with the dashboard and
  the relay says FCM is configured.
- **Logout** of a session drops the tokens that session registered (the app registers again after
  each sign-in, which also moves the token to the new session).
- **Sending**: each detection event goes to every token of every recipient (active users with
  `can_view`, resolved at send time, like Web Push). The existing event JSON is encrypted per token
  with AES-256-GCM (random 12-byte IV, no AAD) and sent as data `{v: "1", iv, ct}` (base64url,
  `ct` = ciphertext + tag), 40 per relay call. Tokens the relay returns as `gone` are deleted;
  `unknown` ones are registered again. Delivery is best-effort, never retried.
- **Relay client** lives with the other dashboard clients (`CentralRuntime`): it exists only while
  the device is paired, so an unpaired device has no mobile push.
- Keys are stored in SQLite like the Web Push subscription keys; tokens and keys are never logged.

## Alternatives considered
- **Send to FCM from the Pi**: puts the project-wide Firebase key on customer hardware.
- **Plain FCM notification payload**: simpler for the app, but Google and the relay would read
  camera names and classes.
- **Tie tokens to session validity**: an expired session would silently stop notifications;
  logout and user deactivation are the explicit stops instead.

## Consequences
- Mobile push needs the dashboard pairing (ADR 0009) and the relay's FCM secret.
- One more outbound call per detection while paired (at most one per 40 phones), within the
  relay's 600 messages per site per hour.
- iOS (APNs) later uses the same routes with `platform: "ios"`.
