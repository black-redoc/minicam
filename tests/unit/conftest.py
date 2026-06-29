"""Unit-test isolation: prevent pydantic-settings from reading the real .env file.

Without this, Settings.model_validate() pulls values from .env, making the
default-field assertions in test_settings.py environment-dependent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_env_file() -> pytest.FixtureFunction:
    """Redirect env_file to a non-existent path so Settings uses only defaults."""
    # Patch the SettingsConfigDict that controls which file pydantic-settings loads.
    # A missing file is silently ignored, so all fields fall back to env vars or
    # model defaults — exactly what the unit tests expect.
    with patch.dict(
        "app.settings.Settings.model_config",
        {"env_file": "/dev/null/nonexistent.env"},
        clear=False,
    ):
        yield
