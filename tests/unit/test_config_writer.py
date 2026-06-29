"""Tests for the config_writer module (.env read/write utilities)."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.services.config_writer as config_writer_module
from app.services.config_writer import read_env_config, write_env_config


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary .env file and redirect _ENV_PATH to it."""
    path = tmp_path / ".env"
    path.write_text(
        "CAMERA_HOST=192.168.1.10\n"
        "CAMERA_PASSWORD=secret\n"
        "CAMERA_USER=admin\n"
    )
    monkeypatch.setattr(config_writer_module, "_ENV_PATH", path)
    return path


class TestReadEnvConfig:
    def test_returns_dict_with_all_keys(self, env_file: Path) -> None:
        result = read_env_config()
        assert "CAMERA_HOST" in result
        assert "CAMERA_PASSWORD" in result
        assert "CAMERA_USER" in result

    def test_returns_correct_values(self, env_file: Path) -> None:
        result = read_env_config()
        assert result["CAMERA_HOST"] == "192.168.1.10"
        assert result["CAMERA_PASSWORD"] == "secret"

    def test_returns_empty_string_for_missing_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / ".env"
        path.write_text("CAMERA_HOST=\n")
        monkeypatch.setattr(config_writer_module, "_ENV_PATH", path)
        result = read_env_config()
        assert result["CAMERA_HOST"] == ""


class TestWriteEnvConfig:
    def test_writes_single_key(self, env_file: Path) -> None:
        write_env_config({"CAMERA_HOST": "10.0.0.1"})
        content = env_file.read_text()
        assert "10.0.0.1" in content

    def test_writes_multiple_keys(self, env_file: Path) -> None:
        write_env_config({"CAMERA_HOST": "10.0.0.5", "CAMERA_USER": "root"})
        content = env_file.read_text()
        assert "10.0.0.5" in content
        assert "root" in content

    def test_applies_chmod_600(self, env_file: Path) -> None:
        write_env_config({"CAMERA_HOST": "10.0.0.1"})
        mode = oct(env_file.stat().st_mode)
        # 0o600 permission bits: owner read+write only
        assert mode.endswith("600")

    def test_preserves_unchanged_keys(self, env_file: Path) -> None:
        """Keys not in the update dict are left as-is."""
        write_env_config({"CAMERA_HOST": "10.0.0.1"})
        result = read_env_config()
        assert result["CAMERA_PASSWORD"] == "secret"
