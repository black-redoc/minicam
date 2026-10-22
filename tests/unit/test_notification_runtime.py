"""Tests for the process-wide notification wiring."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import aiosqlite
import pytest

import app.state as state_module
from app.models.config import AppConfig, CameraConfig, NotificationConfig
from app.repositories.push_subscription_repository import (
    SQLitePushSubscriptionRepository,
)
from app.services.detection.base import Detection
from app.services.detection.worker import DetectionSnapshot
from app.services.notifications import runtime


@pytest.fixture(autouse=True)
def reset_runtime() -> Iterator[None]:
    yield
    runtime.notifier.forget("cam1")
    state_module.detection_results.pop("cam1", None)
    runtime.service.set_push_sender(None)


def _config(mode: str = "inherit", enabled: bool = True) -> AppConfig:
    return AppConfig(
        cameras=[CameraConfig(name="cam1", host="h", notifications=mode)],  # type: ignore[arg-type]
        notifications=NotificationConfig(enabled=enabled, cooldown_seconds=30),
    )


@pytest.mark.parametrize(
    ("mode", "enabled", "expected"),
    [("inherit", True, True), ("inherit", False, False), ("on", False, True)],
)
def test_camera_enabled_reads_current_config(
    mode: str, enabled: bool, expected: bool
) -> None:
    with patch.object(runtime, "get_config", return_value=_config(mode, enabled)):
        assert runtime._camera_enabled("cam1") is expected
        assert runtime._camera_enabled("unknown") is False
        assert runtime._cooldown_seconds() == 30.0


async def test_result_handler_stores_snapshot_and_notifies() -> None:
    snapshot = DetectionSnapshot([Detection("person", 0.9, 0, 0, 1, 1)], sequence=1)
    handler = runtime.detection_result_handler("cam1")
    with (
        patch.object(runtime, "get_config", return_value=_config()),
        runtime.hub.subscribe() as queue,
    ):
        handler(snapshot)
        handler(snapshot)

    assert state_module.detection_results["cam1"] is snapshot
    event = queue.get_nowait()
    assert (event.camera_name, event.classes) == ("cam1", ("person",))


async def test_start_web_push_generates_key_once(
    db_connection: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINICAM_VAPID_SUBJECT", "mailto:ops@example.com")
    await runtime.start_web_push(db_connection)
    sender = runtime.service.push_sender
    assert sender is not None
    first_key = sender.public_key
    stored = await SQLitePushSubscriptionRepository(
        db_connection
    ).get_vapid_private_key()

    await runtime.start_web_push(db_connection)
    second = runtime.service.push_sender
    assert second is not None
    assert second.public_key == first_key
    assert (
        await SQLitePushSubscriptionRepository(db_connection).get_vapid_private_key()
        == stored
    )
    await runtime.shutdown()
    assert runtime.service.push_sender is None


def test_result_handler_feeds_event_recording() -> None:
    snapshot = DetectionSnapshot([Detection("person", 0.9, 0, 0, 1, 1)], sequence=1)
    handler = runtime.detection_result_handler("cam1")
    with (
        patch.object(runtime, "get_config", return_value=_config()),
        patch.object(runtime.recording_runtime.service, "on_snapshot") as on_snapshot,
    ):
        handler(snapshot)
    on_snapshot.assert_called_once_with("cam1", snapshot)
