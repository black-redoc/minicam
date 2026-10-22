"""Read and write config.toml for the camera application."""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w

from app.models.config import (
    AppConfig,
    CameraConfig,
    DetectionConfig,
    NotificationConfig,
)

_CONFIG_PATH: Path = Path(__file__).resolve().parent.parent.parent / "config.toml"


def load_config(path: Path = _CONFIG_PATH) -> AppConfig:
    """Load AppConfig from a TOML file. Returns defaults if file is missing or empty."""
    if not path.exists():
        return AppConfig()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if not data:
        return AppConfig()
    app_section = data.get("app", {})
    cameras = data.get("cameras", [])
    detection_section = data.get("detection", {})
    notifications_section = data.get("notifications", {})
    return AppConfig.model_validate(
        {
            **app_section,
            "cameras": cameras,
            "detection": detection_section,
            "notifications": notifications_section,
        }
    )


def save_config(config: AppConfig, path: Path = _CONFIG_PATH) -> None:
    """Serialise AppConfig to TOML, atomically, with mode 600 from the start.

    The file holds camera passwords, so it is written to a temp file in the
    same directory that is already 0600 before any byte lands in it, fsynced,
    then moved over the target with os.replace(). The target is therefore
    never briefly world-readable, and a crash mid-write leaves the previous
    config intact instead of a truncated one.
    """
    cameras = [cam.model_dump(exclude={"rtsp_url"}) for cam in config.cameras]
    doc: dict[str, object] = {
        "app": {
            "host": config.host,
            "port": config.port,
            "debug": config.debug,
        },
        "cameras": cameras,
        "detection": config.detection.model_dump(),
        "notifications": config.notifications.model_dump(),
    }
    data = tomli_w.dumps(doc).encode()
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def add_camera(camera: CameraConfig, path: Path = _CONFIG_PATH) -> AppConfig:
    """Add a camera to the config. Raises ValueError if name already exists."""
    config = load_config(path)
    if any(c.name == camera.name for c in config.cameras):
        raise ValueError(f"Camera '{camera.name}' already exists")
    config.cameras.append(camera)
    save_config(config, path)
    return config


def update_camera(
    name: str, camera: CameraConfig, path: Path = _CONFIG_PATH
) -> AppConfig:
    """Replace the camera with the given name. Raises KeyError if not found."""
    config = load_config(path)
    for i, c in enumerate(config.cameras):
        if c.name == name:
            config.cameras[i] = camera
            save_config(config, path)
            return config
    raise KeyError(f"Camera '{name}' not found")


def remove_camera(name: str, path: Path = _CONFIG_PATH) -> AppConfig:
    """Remove the camera with the given name. Raises KeyError if not found."""
    config = load_config(path)
    original_count = len(config.cameras)
    config.cameras = [c for c in config.cameras if c.name != name]
    if len(config.cameras) == original_count:
        raise KeyError(f"Camera '{name}' not found")
    save_config(config, path)
    return config


def update_detection_config(
    detection: DetectionConfig, path: Path = _CONFIG_PATH
) -> AppConfig:
    """Replace the detection config section and persist it."""
    config = load_config(path)
    config.detection = detection
    save_config(config, path)
    return config


def update_notification_config(
    notifications: NotificationConfig, path: Path = _CONFIG_PATH
) -> AppConfig:
    """Replace the notifications config section and persist it."""
    config = load_config(path)
    config.notifications = notifications
    save_config(config, path)
    return config
