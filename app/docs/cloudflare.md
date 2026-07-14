# Cloudflare Access & Tunnel

minicam is reachable from outside the local network without opening a port or having a public IP. This page covers how that path is wired end-to-end: `cloudflared`, Cloudflare Access, and the two environment variables (`MINICAM_TRUSTED_PROXIES`, `MINICAM_CF_ACCESS_TEAM_DOMAIN`, `MINICAM_CF_ACCESS_AUD`) that make FastAPI aware of it. See also [Authentication & Authorization](/doc/auth#wan-access-gate) for the `wan_access` grant this layer sits alongside, and [Configuration](/doc/configuration#security-settings) for the full environment variable reference.

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
