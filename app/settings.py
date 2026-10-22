"""Load application configuration from config.toml."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from app.models.config import AppConfig

_CONFIG_PATH: Path = Path(__file__).resolve().parent.parent / "config.toml"


@lru_cache
def get_config(path: str = str(_CONFIG_PATH)) -> AppConfig:
    """Return cached AppConfig singleton. Call get_config.cache_clear() in tests."""
    p = Path(path)
    if not p.exists():
        return AppConfig()
    with open(p, "rb") as f:
        data = tomllib.load(f)
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
