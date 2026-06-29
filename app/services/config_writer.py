"""Read and write .env configuration for the camera application."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values, set_key

# Resolves to the project root .env regardless of working directory.
_ENV_PATH: Path = Path(__file__).resolve().parent.parent.parent / ".env"


def read_env_config() -> dict[str, str]:
    """Read current .env file and return key-value pairs as strings.

    Returns:
        Dictionary with all keys and values from .env. Missing or empty
        values are represented as empty strings.
    """
    raw = dotenv_values(_ENV_PATH)
    return {k: (v or "") for k, v in raw.items()}


def write_env_config(updates: dict[str, str]) -> None:
    """Write key-value pairs to the .env file and apply chmod 600.

    Args:
        updates: Keys and values to write. Existing keys not present in
                 `updates` are left unchanged.
    """
    for key, value in updates.items():
        set_key(str(_ENV_PATH), key, value)
    _ENV_PATH.chmod(0o600)
