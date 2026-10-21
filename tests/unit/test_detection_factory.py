"""Tests for app.services.detection.factory."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.config import AppConfig, DetectionConfig
from app.services.detection.factory import get_detection_backend


@pytest.fixture(autouse=True)
def clear_caches() -> pytest.FixtureFunction:
    get_detection_backend.cache_clear()
    yield
    get_detection_backend.cache_clear()


def _config_with_detection(**overrides: object) -> AppConfig:
    return AppConfig(detection=DetectionConfig(**overrides))


class TestGetDetectionBackendDisabled:
    def test_returns_none_when_backend_is_none(self) -> None:
        config = _config_with_detection(backend="none")
        with patch("app.services.detection.factory.get_config", return_value=config):
            assert get_detection_backend() is None


class TestGetDetectionBackendOnnxValidation:
    def test_returns_none_when_model_path_is_relative(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = _config_with_detection(
            backend="onnx", model_path="relative/model.onnx"
        )
        with patch("app.services.detection.factory.get_config", return_value=config):
            result = get_detection_backend()
        assert result is None
        assert "not an existing absolute file" in caplog.text

    def test_returns_none_when_model_path_does_not_exist(self, tmp_path: Path) -> None:
        config = _config_with_detection(
            backend="onnx", model_path=str(tmp_path / "missing.onnx")
        )
        with patch("app.services.detection.factory.get_config", return_value=config):
            assert get_detection_backend() is None

    def test_returns_none_when_construction_raises(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"not a real model")
        config = _config_with_detection(backend="onnx", model_path=str(model_path))

        with (
            patch("app.services.detection.factory.get_config", return_value=config),
            patch(
                "app.services.detection.factory.OnnxDetectionBackend",
                side_effect=RuntimeError("corrupt model"),
            ),
        ):
            result = get_detection_backend()

        assert result is None

    def test_returns_backend_instance_on_success(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"not a real model")
        config = _config_with_detection(backend="onnx", model_path=str(model_path))

        fake_backend = MagicMock()
        with (
            patch("app.services.detection.factory.get_config", return_value=config),
            patch(
                "app.services.detection.factory.OnnxDetectionBackend",
                return_value=fake_backend,
            ),
        ):
            result = get_detection_backend()

        assert result is fake_backend

    def test_result_is_cached(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"not a real model")
        config = _config_with_detection(backend="onnx", model_path=str(model_path))

        with (
            patch("app.services.detection.factory.get_config", return_value=config),
            patch(
                "app.services.detection.factory.OnnxDetectionBackend",
                return_value=MagicMock(),
            ) as mock_backend_cls,
        ):
            get_detection_backend()
            get_detection_backend()

        mock_backend_cls.assert_called_once()
