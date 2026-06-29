from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from onvif import ONVIFCamera  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_DEFAULT_SPEED = 0.5


@dataclass(frozen=True)
class PTZConfig:
    """ONVIF connection parameters for PTZ control.

    Args:
        host: Camera IP address.
        port: ONVIF port (common default: 8000).
        user: ONVIF username.
        password: ONVIF password.
    """

    host: str
    port: int
    user: str
    password: str


class PTZService:
    """Controls camera pan-tilt-zoom via ONVIF protocol.

    Args:
        config: ONVIF connection parameters.
    """

    def __init__(self, config: PTZConfig) -> None:
        self._config = config
        self._camera: ONVIFCamera | None = None
        self._ptz: Any = None
        self._profile_token: str | None = None

    def connect(self) -> None:
        """Establish ONVIF connection and retrieve the first media profile token."""
        logger.info("Connecting ONVIF PTZ to %s:%d", self._config.host, self._config.port)
        camera = ONVIFCamera(
            self._config.host,
            self._config.port,
            self._config.user,
            self._config.password,
        )
        logger.debug("Creating PTZ service")
        ptz = camera.create_ptz_service()
        logger.debug("Creating media service")
        media = camera.create_media_service()
        logger.debug("Fetching media profiles")
        profiles = media.GetProfiles()
        profile_token = profiles[0].token

        # Only assign state once all steps succeed (atomic connect)
        self._camera = camera
        self._ptz = ptz
        self._profile_token = profile_token
        logger.info("ONVIF PTZ connected — profile token: %s", profile_token)

    def is_connected(self) -> bool:
        """Return True if ONVIF session is active."""
        return self._ptz is not None and self._profile_token is not None

    def disconnect(self) -> None:
        """Release ONVIF resources."""
        self._ptz = None
        self._camera = None
        self._profile_token = None

    def up(self) -> None:
        """Tilt camera upward."""
        self._continuous_move(pan=0.0, tilt=_DEFAULT_SPEED)

    def down(self) -> None:
        """Tilt camera downward."""
        self._continuous_move(pan=0.0, tilt=-_DEFAULT_SPEED)

    def left(self) -> None:
        """Pan camera to the left."""
        self._continuous_move(pan=-_DEFAULT_SPEED, tilt=0.0)

    def right(self) -> None:
        """Pan camera to the right."""
        self._continuous_move(pan=_DEFAULT_SPEED, tilt=0.0)

    def stop(self) -> None:
        """Stop all PTZ movement."""
        self._assert_connected()
        request = self._ptz.create_type("Stop")
        request.ProfileToken = self._profile_token
        request.PanTilt = True
        request.Zoom = True
        self._ptz.Stop(request)

    def _continuous_move(self, pan: float, tilt: float) -> None:
        self._assert_connected()
        request = self._ptz.create_type("ContinuousMove")
        request.ProfileToken = self._profile_token
        request.Velocity = {
            "PanTilt": {"x": pan, "y": tilt},
        }
        self._ptz.ContinuousMove(request)

    def _assert_connected(self) -> None:
        if self._ptz is None or self._profile_token is None:
            raise RuntimeError("PTZService is not connected. Call connect() first.")
