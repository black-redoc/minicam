"""Tests for app.services.model_catalog (minicam-models manifest parsing)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.model_catalog import (
    DEFAULT_CATALOG_URL,
    InvalidManifestError,
    catalog_url,
    parse_manifest,
)

_MAX = 200 * 1024 * 1024
_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "minicam_models_manifest_v0.1.0.json"
)


@pytest.fixture()
def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE.read_text())
    return copy.deepcopy(data)


class TestParseManifest:
    def test_parses_the_published_v0_1_0_manifest(
        self, manifest: dict[str, Any]
    ) -> None:
        catalog = parse_manifest(manifest, _MAX)

        assert catalog.release == "v0.1.0"
        assert len(catalog.assets) == 8
        names = {asset.name for asset in catalog.assets}
        assert "yolo11n-int8.onnx" in names
        assert all(asset.url.startswith("https://") for asset in catalog.assets)

    @pytest.mark.parametrize("data", [None, [], "manifest"])
    def test_rejects_non_object(self, data: object) -> None:
        with pytest.raises(InvalidManifestError, match="not a JSON object"):
            parse_manifest(data, _MAX)

    def test_rejects_unknown_schema_version(self, manifest: dict[str, Any]) -> None:
        manifest["schema_version"] = 2
        with pytest.raises(InvalidManifestError, match="schema_version"):
            parse_manifest(manifest, _MAX)

    def test_rejects_missing_contract(self, manifest: dict[str, Any]) -> None:
        del manifest["contract"]
        with pytest.raises(InvalidManifestError, match="contract"):
            parse_manifest(manifest, _MAX)

    @pytest.mark.parametrize(
        ("path", "value", "problem"),
        [
            (("input", "shape"), [1, 3, 320, 320], "input shape"),
            (("output", "shape"), [1, 300, 6], "output shape"),
            (("classes",), "custom", "classes"),
            (("nms",), True, "built-in NMS"),
        ],
    )
    def test_rejects_incompatible_contract(
        self,
        manifest: dict[str, Any],
        path: tuple[str, ...],
        value: object,
        problem: str,
    ) -> None:
        target = manifest["contract"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(InvalidManifestError, match=problem):
            parse_manifest(manifest, _MAX)

    def test_rejects_missing_assets(self, manifest: dict[str, Any]) -> None:
        manifest["assets"] = None
        with pytest.raises(InvalidManifestError, match="no assets"):
            parse_manifest(manifest, _MAX)

    def test_rejects_malformed_asset(self, manifest: dict[str, Any]) -> None:
        del manifest["assets"][0]["sha256"]
        with pytest.raises(InvalidManifestError, match="Malformed asset"):
            parse_manifest(manifest, _MAX)

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("name", "../evil.onnx", "Invalid asset name"),
            ("name", "model.bin", "Invalid asset name"),
            ("url", "http://example.com/model.onnx", "not https"),
            ("sha256", "abc", "invalid sha256"),
            ("sha256", "A" * 64, "invalid sha256"),
            ("bytes", 0, "invalid size"),
            ("bytes", _MAX + 1, "invalid size"),
        ],
    )
    def test_rejects_unsafe_asset(
        self, manifest: dict[str, Any], field: str, value: object, reason: str
    ) -> None:
        manifest["assets"][0][field] = value
        with pytest.raises(InvalidManifestError, match=reason):
            parse_manifest(manifest, _MAX)


class TestCatalogUrl:
    def test_defaults_to_latest_minicam_models_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MINICAM_MODEL_CATALOG_URL", raising=False)
        assert catalog_url() == DEFAULT_CATALOG_URL
        assert "black-redoc/minicam-models/releases/latest" in DEFAULT_CATALOG_URL

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pinned = "https://github.com/x/y/releases/download/v1/manifest.json"
        monkeypatch.setenv("MINICAM_MODEL_CATALOG_URL", pinned)
        assert catalog_url() == pinned
