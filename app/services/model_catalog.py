"""Parse and validate a minicam-models release manifest.

minicam-models (https://github.com/black-redoc/minicam-models) publishes
YOLO models already exported to the contract OnnxDetectionBackend expects,
plus a manifest.json describing every asset with its size and sha256. The
dashboard lists those assets and installs one on request; this module only
turns untrusted manifest JSON into validated CatalogAsset records.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.models.config import CatalogAsset, ModelCatalog
from app.services.detection.onnx_backend import MODEL_INPUT_SIZE

DEFAULT_CATALOG_URL = (
    "https://github.com/black-redoc/minicam-models/releases/latest/download/"
    "manifest.json"
)
_CATALOG_URL_ENV = "MINICAM_MODEL_CATALOG_URL"
_SUPPORTED_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# What OnnxDetectionBackend decodes: 4 box coords + 80 COCO class scores
# for each of 8400 anchors, no built-in NMS.
_EXPECTED_INPUT_SHAPE = [1, 3, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE]
_EXPECTED_OUTPUT_SHAPE = [1, 84, 8400]


class InvalidManifestError(ValueError):
    """The manifest is malformed, unsupported, or not compatible with minicam."""


def catalog_url() -> str:
    """Manifest URL, overridable via MINICAM_MODEL_CATALOG_URL (e.g. a fork
    or a release tag pinned instead of `latest`)."""
    return os.environ.get(_CATALOG_URL_ENV) or DEFAULT_CATALOG_URL


def parse_manifest(data: object, max_asset_bytes: int) -> ModelCatalog:
    """Validate manifest JSON and return its assets.

    Rejects the whole manifest if its declared contract does not match what
    OnnxDetectionBackend can run, and any asset whose name could escape the
    models directory, whose URL is not https, whose checksum is not a sha256
    hex digest, or whose size exceeds max_asset_bytes.

    Raises:
        InvalidManifestError: with a reason suitable for an API error.
    """
    if not isinstance(data, dict):
        raise InvalidManifestError("Manifest is not a JSON object")
    if data.get("schema_version") != _SUPPORTED_SCHEMA_VERSION:
        raise InvalidManifestError(
            f"Unsupported manifest schema_version {data.get('schema_version')!r}"
        )
    _check_contract(data.get("contract"))

    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list):
        raise InvalidManifestError("Manifest has no assets list")

    try:
        assets = [CatalogAsset.model_validate(raw) for raw in raw_assets]
    except ValueError as exc:
        raise InvalidManifestError(f"Malformed asset entry: {exc}") from exc
    for asset in assets:
        _check_asset(asset, max_asset_bytes)
    return ModelCatalog(release=str(data.get("release", "")), assets=assets)


def _check_contract(contract: object) -> None:
    if not isinstance(contract, dict):
        raise InvalidManifestError("Manifest has no contract section")
    problems = []
    if contract.get("input", {}).get("shape") != _EXPECTED_INPUT_SHAPE:
        problems.append("input shape")
    if contract.get("output", {}).get("shape") != _EXPECTED_OUTPUT_SHAPE:
        problems.append("output shape")
    if contract.get("classes") != "coco80":
        problems.append("classes")
    if contract.get("nms") is not False:
        problems.append("built-in NMS")
    if problems:
        raise InvalidManifestError(
            "Release is not compatible with this minicam: " + ", ".join(problems)
        )


def _check_asset(asset: CatalogAsset, max_asset_bytes: int) -> None:
    if Path(asset.name).name != asset.name or not asset.name.endswith(".onnx"):
        raise InvalidManifestError(f"Invalid asset name {asset.name!r}")
    if not asset.url.startswith("https://"):
        raise InvalidManifestError(f"Asset {asset.name!r} URL is not https")
    if not _SHA256_RE.match(asset.sha256):
        raise InvalidManifestError(f"Asset {asset.name!r} has an invalid sha256")
    if not 0 < asset.bytes <= max_asset_bytes:
        raise InvalidManifestError(f"Asset {asset.name!r} has an invalid size")
