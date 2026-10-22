# Cloudflare Access & Tunnel

minicam is reachable from outside the local network without opening a port or having a public IP. This page covers how that path is wired end-to-end: `cloudflared`, Cloudflare Access, and the three environment variables (`MINICAM_TRUSTED_PROXIES`, `MINICAM_CF_ACCESS_TEAM_DOMAIN`, `MINICAM_CF_ACCESS_AUD`) that make FastAPI aware of it. See also [Authentication & Authorization](/doc/auth#wan-access-gate) for the `wan_access` grant this layer sits alongside, and [Configuration](/doc/configuration#security-settings) for the full environment variable reference.

## Setup, step by step

Prerequisites: a domain whose DNS is managed by Cloudflare, a Cloudflare Zero Trust organization (its team name gives you `https://<team>.cloudflareaccess.com`), and minicam already running under systemd with a valid `.env` (see [Configuration](/doc/configuration#initial-setup-creating-minicam_jwt_secret)).

### 1. Install `cloudflared` on the Pi

From Cloudflare's signed apt repository (Debian / Raspberry Pi OS, `arm64` and `amd64`):

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install cloudflared
```

### 2. Create the tunnel

1. Zero Trust dashboard -> **Networks -> Tunnels -> Create a tunnel** -> type **Cloudflared**, name it (e.g. `minicam-<site>`).
2. The dashboard shows an install command with a token. Run it on the Pi:

   ```bash
   sudo cloudflared service install <TOKEN>
   ```

   The token is a credential for this tunnel: don't commit it, paste it in chats, or put it in `.env`. If it leaks, rotate it from the tunnel's page and re-run the command.
3. Confirm the connector is healthy (the dashboard should show the tunnel as **Healthy**):

   ```bash
   systemctl status cloudflared.service
   journalctl -u cloudflared -n 50
   ```

### 3. Route a hostname to minicam

In the tunnel -> **Public Hostname -> Add a public hostname**:

| Field | Value |
|---|---|
| Subdomain / Domain | e.g. `cam` . `example.com` |
| Service type | `HTTP` |
| URL | `127.0.0.1:8080` |

Use **`127.0.0.1`**, not `localhost`. `MINICAM_TRUSTED_PROXIES` matches the exact client IP string, so if `cloudflared` reached the app over IPv6 (`::1`) its requests would not match the trusted proxy and — being loopback — would be classified as **LAN**, skipping the WAN gate and the Access JWT check (see [`MINICAM_TRUSTED_PROXIES`](#minicam_trusted_proxies) below). WebSockets (used by the video stream) work through the tunnel with no extra setting.

### 4. Protect the hostname with Cloudflare Access

1. **Access -> Applications -> Add an application -> Self-hosted**. Application domain: the exact hostname from step 3.
2. Add a policy with action **Allow** and an **Include -> Emails** rule listing every person who should reach minicam from outside. Each email should be the same one that person uses to log in to minicam — FastAPI rejects mismatches.
3. Save, then open the application's **Overview** and copy the **Application Audience (AUD) Tag**.

Do this *before* sharing the hostname: until the Access application exists, the tunnel serves minicam's login page publicly.

### 5. Configure minicam

In `.env`:

```bash
MINICAM_TRUSTED_PROXIES=127.0.0.1
MINICAM_CF_ACCESS_TEAM_DOMAIN=https://<team>.cloudflareaccess.com
MINICAM_CF_ACCESS_AUD=<AUD tag from step 4>
```

Then `sudo systemctl restart minicam.service`.

### 6. Grant `wan_access` to each remote user

On the **Admin** page, tick **WAN access** on each user who should reach minicam from outside — including your own account if you're an admin. Do it from the LAN: until it's ticked, the WAN gate blocks that user. Untick it to revoke. See [Authentication & Authorization](/doc/auth#granting-wan-access-admin-only).

### 7. Verify

```bash
# Unauthenticated requests must be stopped by Access (302 to the Access login):
curl -sI https://cam.example.com | head -3
# The JWKS minicam uses to verify Access JWTs must be reachable from the Pi:
curl -s https://<team>.cloudflareaccess.com/cdn-cgi/access/certs | head -c 200
```

Then, from a phone off Wi-Fi: open the hostname, pass the Access login, log in to minicam, and open a camera.

| Symptom | Likely cause |
|---|---|
| Cloudflare error `502` / `1033` | minicam not running, or the service URL isn't `127.0.0.1:8080` |
| Page loads without an Access login | Access application missing or its domain doesn't match the hostname |
| `403` on every WAN request | `MINICAM_CF_ACCESS_AUD` / `MINICAM_CF_ACCESS_TEAM_DOMAIN` wrong or unset, or the Access email differs from the minicam account's email |
| `403` "WAN access is not enabled for this account" | step 6 not done for that user |
| `403` "Invalid Cloudflare Access token" (dashboard loads, video and PTZ fail) | `MINICAM_CF_ACCESS_TEAM_DOMAIN` / `MINICAM_CF_ACCESS_AUD` don't match the Access application. The server log line `Rejected Cloudflare Access token: …` shows the token's `aud`/`iss` next to the expected ones; copy the real values into `.env` and restart (re-export `.env` first if you run `fastapi dev`) |
| Remote users skip the WAN gate (only if the app also listens on IPv6) | the tunnel targets `localhost`/`::1` instead of `127.0.0.1` |

## Mobile apps

The native apps (minicam-mobile) can't complete Access's login page themselves, and they can't read the `CF_Authorization` cookie from the system browser. Instead they open `GET /api/mobile/access-handoff?state=<random>` in the system browser (Custom Tabs on Android, `ASWebAuthenticationSession` on iOS):

1. Access stops the request and shows its login, exactly as for the browser.
2. Once the user is through, FastAPI verifies the forwarded `Cf-Access-Jwt-Assertion` with the same `CloudflareAccessVerifier` used for every WAN request, and answers `302` to `minicam://access-callback?state=<state>#token=<that JWT>`. The redirect target is fixed server-side (no open redirect), and the token goes in the URL fragment, so it doesn't end up in access logs.
3. The app checks that `state` matches the one it sent, stores the token in the Keychain / Android Keystore, and sends it as the `cf-access-token` header on every request and WebSocket handshake. Cloudflare accepts that header in place of the cookie and forwards the identity to FastAPI as usual.

The token grants nothing by itself: the app still logs in with the user's minicam password, and the email in the Access token must match that user, as above. Nothing needs to be configured for this beyond the setup steps above.

## The Tunnel

`cloudflared` runs as a systemd service (`cloudflared.service`, installed with `cloudflared service install <token>`) on the same machine as the FastAPI app. It opens an outbound-only connection to Cloudflare and maps a hostname on your own domain to `localhost:8080` — there is no port forwarding, no public IP, and no inbound firewall rule. The tunnel and its route are managed entirely from the Cloudflare Zero Trust dashboard (Networks -> Tunnels), not from a `config.yml` on this machine.

Because the tunnel is a systemd unit of its own (already `Restart=on-failure` and `enabled` from the installer, plus a `cloudflared-update.timer` that keeps the binary current), minicam's own deployment (see the repo's `README.md`) only needs to supervise the FastAPI process (`deploy/minicam.service`) — there is no second unit to add here.

## Cloudflare Access: the first filter

The tunnel hostname is protected by a Cloudflare Access **Application**, configured in the Zero Trust dashboard under **Access -> Applications -> Policies**. The policy's rule is an **Emails** whitelist — an explicit list of allowed addresses. Anyone else is stopped by Cloudflare's own login page before a single byte reaches FastAPI. This is the first and coarsest filter: it doesn't know about minicam's users table, camera permissions, or `wan_access` grants — it only knows "is this email on the list."

## Verifying identity in FastAPI: the second filter

Passing Access's login page is not the same as minicam recognizing you. On every WAN request, Cloudflare forwards the signed identity it just verified in a `Cf-Access-Jwt-Assertion` header. `app/services/cloudflare_access.py` (`CloudflareAccessVerifier`) checks that JWT cryptographically:

- Fetches Cloudflare's public signing keys (JWKS) from `<team-domain>/cdn-cgi/access/certs` via `jwt.PyJWKClient`.
- Verifies the `RS256` signature, the `aud` claim against `MINICAM_CF_ACCESS_AUD`, and the `iss` claim against `MINICAM_CF_ACCESS_TEAM_DOMAIN`.
- Extracts the `email` claim.

`app/dependencies.py` (`enforce_wan_access_gate` / `_require_matching_access_identity`) then requires that email to match the email of the already-authenticated session user. If Access isn't configured (`MINICAM_CF_ACCESS_TEAM_DOMAIN` / `MINICAM_CF_ACCESS_AUD` missing), the header is absent, the JWT fails verification, or the emails don't match, the request gets **`403`** — this fails closed, the same posture as a missing `MINICAM_JWT_SECRET` at startup.

**This does not replace minicam's own login.** A valid Access identity only adds a second, network-verified factor on top of the existing session; it never grants access on its own. A visitor still needs an active session (`/api/auth/login`) *and*, on WAN, a `wan_access` grant *and* an Access JWT whose email matches that session's user.

## `MINICAM_TRUSTED_PROXIES`

`app/services/network.py` (`classify_network`) decides whether a request is LAN or WAN by looking at the direct client IP. Since `cloudflared` runs on this same machine, every WAN request it forwards arrives at FastAPI from `127.0.0.1` — indistinguishable, by IP alone, from a LAN request made from the Pi itself. Setting `MINICAM_TRUSTED_PROXIES=127.0.0.1` tells `classify_network` to treat connections from that address as WAN rather than LAN. This is safe specifically because no real LAN client ever connects through `cloudflared`; `127.0.0.1` in this deployment only ever means "arrived through the tunnel."

## How the pieces stack

```
Browser --(HTTPS, custom domain)--> Cloudflare Access (Emails policy)
                                            |
                                    Cloudflare Tunnel
                                            |
                                   cloudflared (127.0.0.1)
                                            |
                                  FastAPI :8080 on the Pi
                                    - classify_network -> WAN (trusted proxy)
                                    - session cookie -> logged-in user
                                    - Cf-Access-Jwt-Assertion -> verified email
                                    - wan_access grant -> allowed?
```

This mirrors the "WAN Access Path (Cloudflare)" diagram in `spec.md`'s High-Level Architecture section, and the rules in `spec.md` section 9 ("WAN Access via Cloudflare") — with one deliberate difference from that original sketch: authorization here stays against the local SQLite tables (`users`, `camera_permissions`, `wan_access`), not Cloudflare D1, since the multi-tenant D1 migration (`spec.md` section 11) hasn't happened yet.

## LAN is unaffected

None of this touches LAN traffic. A request whose direct IP is private or loopback (and isn't `127.0.0.1` through the trusted-proxy path) is classified as LAN, never looks at the `Cf-Access-Jwt-Assertion` header, and keeps working over plain HTTP even if the tunnel or Access is misconfigured or down.
