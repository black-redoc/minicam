# Camera Discovery

minicam can automatically detect ONVIF-compatible cameras on your local network using **WS-Discovery** (Web Services Discovery).

## How It Works

WS-Discovery sends a UDP multicast probe to the standard ONVIF multicast address (`239.255.255.250:3702`). ONVIF cameras that support WS-Discovery respond with their endpoint URLs and device metadata.

minicam then resolves each response to an IP address and retrieves basic device information (name, hardware model, location).

## Endpoint

```
GET /api/setup/discover
```

Requires an administrator session (see [Authentication & Authorization](/doc/auth)) — `401` with no session, `403` for a non-admin user.

No parameters required. The scan takes a few seconds while waiting for camera responses.

## Response

Returns a JSON array of discovered devices:

```json
[
  {
    "host": "192.168.1.100",
    "name": "IPCamera",
    "hardware": "IPCamera",
    "location": "192.168.1.100"
  },
  {
    "host": "192.168.1.105",
    "name": "DS-2CD2143G2-I",
    "hardware": "DS-2CD2143G2-I",
    "location": "192.168.1.105"
  }
]
```

| Field | Description |
|-------|-------------|
| `host` | IP address of the discovered camera |
| `name` | Device name reported by the camera |
| `hardware` | Hardware model string |
| `location` | Location metadata (often the IP) |

## Using Discovery Results in the UI

1. Open the dashboard and click the **Settings** icon.
2. Click **Discover cameras** to trigger the scan.
3. Discovered cameras appear as a list with a **+ Add** button.
4. Clicking **+ Add** pre-fills the camera form with the discovered IP.
5. Enter credentials and click **Save** to add the camera.

## Limitations

- WS-Discovery requires the minicam server to be on the **same Layer 2 network** as the cameras (multicast does not cross routers without IGMP proxy).
- Cameras that do not implement WS-Discovery will not appear. You can still add them manually.
- The scan has a fixed timeout of a few seconds — cameras that respond slowly may be missed.

## Implementation

The discovery service lives in `app/services/discovery.py` and uses the `wsdiscovery` Python library. Results are filtered and deduplicated before being returned to the client.
