"""Unit-test isolation: prevent get_config() from reading the real config.toml."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.config import AppConfig


@pytest.fixture(autouse=True)
def isolate_config() -> pytest.FixtureFunction:
    """Prevent get_config() from reading the real config.toml during unit tests."""
    with patch("app.settings.get_config", return_value=AppConfig()):
        yield
