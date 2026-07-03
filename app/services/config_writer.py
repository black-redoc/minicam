"""Read and write config.toml for the camera application."""

from __future__ import annotations

import tomllib
import tomli_w
from pathlib import Path

from app.models.config import AppConfig, CameraConfig

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
    return AppConfig.model_validate({**app_section, "cameras": cameras})


def save_config(config: AppConfig, path: Path = _CONFIG_PATH) -> None:
    """Serialise AppConfig to TOML and apply chmod 600."""
    cameras = [cam.model_dump(exclude={"rtsp_url"}) for cam in config.cameras]
    doc: dict[str, object] = {
        "app": {
            "host": config.host,
            "port": config.port,
            "debug": config.debug,
        },
        "cameras": cameras,
    }
    path.write_bytes(tomli_w.dumps(doc).encode())
    path.chmod(0o600)


def add_camera(camera: CameraConfig, path: Path = _CONFIG_PATH) -> AppConfig:
    """Add a camera to the config. Raises ValueError if name already exists."""
    config = load_config(path)
    if any(c.name == camera.name for c in config.cameras):
        raise ValueError(f"Camera '{camera.name}' already exists")
    config.cameras.append(camera)
    save_config(config, path)
    return config


def update_camera(name: str, camera: CameraConfig, path: Path = _CONFIG_PATH) -> AppConfig:
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
