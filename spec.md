# Camera Dashboard Specification

Version: 0.2.0

---

# 1. Overview

## Objective

Develop a modern, low-latency video surveillance dashboard for ip cameras using Python.

The application must provide:

- Live video and audio streaming
- PTZ (Pan-Tilt-Zoom) controls through ONVIF
- Low-latency streaming
- Web dashboard
- Future AI integration (YOLO, PyTorch)
- Scalability to multiple cameras
- High test coverage
- Production-ready architecture

---

# 2. Technology Stack

Backend

- Python 3.13+
- FastAPI
- Uvicorn

Streaming

- FFmpeg
- PyAV

Frontend

- HTML5
- CSS3
- Vanilla JavaScript

Camera Protocols

- RTSP
- ONVIF

Testing

- pytest
- pytest-asyncio
- pytest-cov
- pytest-mock

Quality

- Ruff
- Black
- MyPy
- pre-commit

Benchmarking

- pytest-benchmark

Performance Analysis

- Scalene

Load Testing

- Locust

Edge / WAN Access

- Cloudflare Tunnel
- Cloudflare Access

Database

- Cloudflare D1

Mobile

- React Native
- WebSocket client (current)
- WebRTC client (future)

Push Notifications

- APNs (iOS)
- Firebase Cloud Messaging (Android)

Future Real-Time Transport

- Cloudflare Calls (SFU)
- TURN
- WebRTC

---

# 3. High-Level Architecture

                IP Camera
                     │
               RTSP / ONVIF
                     │
             RTSPCamera Service
                     │
                Decoder Service
                     │
                Frame Model
                     │
          DoubleFrameBuffer
                     │
    ┌────────────────┼────────────────┐
    │                │                │

Dashboard YOLO Service Recorder
│ │ │
Snapshot Analytics Future AI

## WAN Access Path (Cloudflare)

Raspberry Pi (Dashboard / API)
│
Cloudflare Tunnel
│
Cloudflare Access (identity check)
│
FastAPI (authorization check against D1)
│
┌──────────────┴──────────────┐
│ │
Web Client Mobile Client (React Native)

FastAPI queries Cloudflare D1 (users, plans, subscriptions,
cameras, camera_pi_assignment, camera_permissions) on every
WAN request.

## LAN Access Path (unchanged)

Browser on local network → FastAPI → Dashboard / WebSocket

LAN access does not pass through Cloudflare and remains
available regardless of subscription status.

## AI / Notification Path

Frame → YOLO Service → detection (within active schedule)
→ Push Service (APNs / FCM) → Mobile Client

---

# 4. Directory Structure

app/

    buffers/

        double_frame_buffer.py

    models/

        frame.py

    services/

        camera.py

        decoder.py

        ptz.py

    routers/

    templates/

    static/

        css/

        js/

tests/

    unit/

    integration/

    e2e/

    benchmarks/

    fixtures/

---

# 5. Components

## Frame

Purpose

Represents a decoded video frame.

Properties

- image
- width
- height
- timestamp
- sequence

Requirements

- Immutable
- Shared across services
- No business logic

---

## DoubleFrameBuffer

Purpose

Store the latest decoded frame.

Characteristics

- Double buffering
- Low latency
- Thread-safe
- Single producer
- Multiple readers

Responsibilities

- Write latest frame
- Read latest frame
- Swap buffers atomically

Non-Responsibilities

- Image decoding
- Frame processing
- Synchronization between consumers

---

## RTSPCamera

Responsibilities

- Connect to RTSP server
- Receive packets
- Decode frames
- Create Frame objects
- Push frames to DoubleFrameBuffer

Non-Responsibilities

- PTZ
- Dashboard
- AI
- Recording

---

## Decoder

Responsibilities

Convert PyAV frames into NumPy arrays.

Future

Support

- CPU decoding
- Raspberry Pi hardware decoding
- VAAPI
- CUDA

---

## PTZ Service

Responsibilities

Move camera

Supported operations

- Up
- Down
- Left
- Right
- Stop
- Presets (future)

Communication

ONVIF

---

# 6. Dashboard

Must display

- Live video
- Audio
- Camera information
- FPS
- Connection status

Controls

↑

← ○ →

↓

Future

- Snapshot
- Recording
- AI detections
- Presets
- Fullscreen

---

# 7. Streaming Pipeline

Camera

↓

RTSP

↓

PyAV

↓

Decoder

↓

Frame

↓

DoubleFrameBuffer

↓

WebSocket

↓

Browser

Target latency

<150 ms

---

# 8. AI Pipeline (Future)

Frame

↓

YOLO

↓

Bounding Boxes

↓

Dashboard

Requirements

- No modifications to original Frame
- AI works on copied images

---

# 9. WAN Access via Cloudflare

Purpose

Allow authenticated users to view cameras from outside the local network without exposing the Raspberry Pi to the public internet.

Mechanism

- Each Raspberry Pi runs a private `cloudflared` tunnel (no public IP, no port forwarding).
- Cloudflare Access protects the tunnel hostname and requires identity verification before any request reaches the application.
- FastAPI resolves the caller identity from the Cloudflare Access JWT and authorizes the request against Cloudflare D1 (see Section 11).

Rules

- LAN access does not go through Cloudflare and keeps working regardless of subscription status.
- WAN access is denied automatically if the Access JWT is invalid or the subscription is not active.

Future

- Replace the WebSocket transport with WebRTC over Cloudflare Calls (SFU) + TURN for lower latency and better NAT traversal on mobile clients.

---

# 10. Authorization and Camera Access Control

Purpose

Guarantee that a user can only see and control the cameras they are explicitly allowed to, even if the stream URL is discovered or guessed.

Permission model

- Permissions are granted per (user, camera) pair.
- `can_view` and `can_use_ptz` are independent flags.
- Deny by default: no explicit permission record means no access.

Enforcement

- Every stream and PTZ request is authorized server-side against Cloudflare D1, using the identity resolved from the Cloudflare Access JWT.
- Camera stream URLs use short-lived signed tokens instead of static, predictable identifiers.
- URL obscurity is never treated as a security control.

---

# 11. Multi-Tenancy, Plans and Subscriptions (Cloudflare D1)

Purpose

Support selling the service to multiple users, each with a plan, a set of cameras, and an audit trail of payment status.

Entities

- `users` — account identity, linked to Cloudflare Access login.
- `plans` — name, camera limit, price, features.
- `subscriptions` — user ↔ plan, status (`active` / `past_due` / `canceled`), dates.
- `payment_audit` — history of payment events, used to audit when WAN streaming was enabled or disabled for a user.
- `cameras` — logical camera (name, location).
- `raspberry_pis` — physical Pi (hostname of its Cloudflare tunnel).
- `camera_pi_assignment` — which camera is served by which Raspberry Pi.
- `camera_permissions` — (user, camera, can_view, can_use_ptz), see Section 10.

Rule

The WAN authorization middleware checks `subscriptions.status`. Only an `active` subscription allows WAN streaming. Subscription status never affects LAN streaming.

---

# 12. Mobile Client (React Native)

Purpose

Provide real-time camera viewing from a mobile device.

Characteristics

- Built with React Native.
- Authenticates through Cloudflare Access; the resulting JWT is sent with every API request.
- Consumes live streaming over WebSocket (current) and WebRTC via Cloudflare Calls SFU + TURN (future).
- Registers a push token (APNs or FCM) per device to receive YOLO alerts.
- Only lists and renders cameras the authenticated user is authorized to view (Section 10).

---

# 13. YOLO Scheduled Detection and Push Notifications

Purpose

Notify users in real time when YOLO detects an event, without spamming outside of relevant hours.

Rules

- Detection schedules are configurable per camera.
- Outside the active schedule window, detections do not trigger push notifications.
- On a detection within the active window, the service resolves the list of users authorized to view that camera (`camera_permissions`) and dispatches a push notification to each of their registered devices.

Delivery

- iOS devices receive notifications through APNs.
- Android devices receive notifications through Firebase Cloud Messaging (FCM).

---

# 14. Testing Strategy

## Unit Tests

Coverage target

> 95%

Components

- Frame
- DoubleFrameBuffer
- Decoder
- RTSPCamera
- PTZ

---

## Integration Tests

Components

FastAPI

↓

RTSPCamera

↓

DoubleFrameBuffer

↓

WebSocket

---

## End-to-End

Browser

↓

Dashboard

↓

FastAPI

↓

Camera

---

## Benchmarks

Measure

- Frame write latency
- Frame read latency
- Buffer swap latency
- Frames per second
- Multi-reader performance

---

# 15. Coding Standards

Language

English

Comments

English

Docstrings

Google Style

Typing

Required

Rules

- No global variables
- No mutable shared state
- Dependency Injection
- Small classes
- Single Responsibility Principle

---

# 16. Concurrency

Preferred API

concurrent.futures.ThreadPoolExecutor

Avoid

threading.Thread

Synchronization

Lock only when swapping buffers

Never

- Busy waiting
- Long critical sections

---

# 17. Performance Goals

Streaming

30 FPS minimum

Latency

<150 ms

Memory

Stable over 24 hours

CPU

Optimized for Raspberry Pi

---

# 18. Future Roadmap

Phase 1

- RTSP streaming
- Dashboard
- PTZ

Phase 2

- Audio
- Snapshot
- Recording

Phase 3

- YOLO
- Object tracking
- Alerts

Phase 4

- Multiple cameras
- User authentication
- Camera groups

Phase 5

- Docker
- Kubernetes
- Cloud deployment

Phase 6

- Cloudflare Tunnel + Cloudflare Access for WAN streaming
- Cloudflare D1: users, plans, subscriptions, payment audit
- Per-camera authorization (view / PTZ) with signed stream tokens
- React Native mobile client
- YOLO detection schedules with APNs / FCM push notifications

Phase 7

- WebRTC streaming over Cloudflare Calls (SFU) + TURN

---

# 19. Quality Gates

Every Pull Request must pass

- Ruff
- Black
- MyPy
- Unit Tests
- Integration Tests
- Coverage >95%

---

# 20. Design Principles

- SOLID
- Clean Architecture
- Dependency Injection
- Low Coupling
- High Cohesion
- Test-Driven Development
- Immutable Domain Objects
- Composition over Inheritance
