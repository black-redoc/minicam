"""Standalone migration script: reads .env and writes config.toml.

Usage:
    python migrate_env_to_toml.py

Exits with an error if config.toml already exists.
Only stdlib dependencies: pathlib, tomllib, tomli_w.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import tomli_w

_ROOT = Path(__file__).resolve().parent
_ENV_PATH = _ROOT / ".env"
_TOML_PATH = _ROOT / "config.toml"

_DEFAULTS: dict[str, str | int | bool] = {
    "CAMERA_RTSP_PORT": 554,
    "CAMERA_ONVIF_PORT": 80,
    "CAMERA_USER": "admin",
    "CAMERA_PASSWORD": "",
    "CAMERA_CHANNEL": 1,
    "CAMERA_STREAM": "main",
    "APP_HOST": "0.0.0.0",
    "APP_PORT": 8080,
    "DEBUG": False,
}


def _parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


def _build_toml_doc(env: dict[str, str]) -> dict[str, object]:
    def get(key: str) -> str:
        return env.get(key, str(_DEFAULTS.get(key, "")))

    return {
        "app": {
            "host": get("APP_HOST"),
            "port": int(get("APP_PORT")),
            "debug": get("DEBUG").lower() in ("true", "1", "yes"),
        },
        "cameras": [
            {
                "name": "default",
                "host": get("CAMERA_HOST"),
                "rtsp_port": int(get("CAMERA_RTSP_PORT")),
                "onvif_port": int(get("CAMERA_ONVIF_PORT")),
                "user": get("CAMERA_USER"),
                "password": get("CAMERA_PASSWORD"),
                "channel": int(get("CAMERA_CHANNEL")),
                "stream": get("CAMERA_STREAM"),
            }
        ],
    }


def main() -> None:
    if _TOML_PATH.exists():
        print(f"Error: {_TOML_PATH} already exists. Remove it first.", file=sys.stderr)
        sys.exit(1)

    if not _ENV_PATH.exists():
        print(f"Error: {_ENV_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    env = _parse_env(_ENV_PATH)
    doc = _build_toml_doc(env)
    _TOML_PATH.write_bytes(tomli_w.dumps(doc).encode())
    _TOML_PATH.chmod(0o600)
    print(f"Written: {_TOML_PATH}")


if __name__ == "__main__":
    main()
