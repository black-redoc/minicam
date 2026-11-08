"""Unit tests for app/services/setup_claim.py (LED setup claim, ADR 0006)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from app.services.setup_claim import (
    CODE_INTERVAL_SECONDS,
    CODE_TTL_SECONDS,
    MAX_CODES_PER_WINDOW,
    WINDOW_SECONDS,
    ClaimMode,
    InvalidCodeError,
    LedUnavailableError,
    SetupClaimService,
    SysfsLed,
    TooManyCodesError,
    TooSoonError,
    WindowClosedError,
    blink_schedule,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeLed:
    def __init__(self) -> None:
        self.shown: list[str] = []
        self.stopped = 0

    async def show(self, code: str) -> None:
        self.shown.append(code)

    async def stop(self) -> None:
        self.stopped += 1


def make_service(
    clock: FakeClock, led: FakeLed, digits: str = "1234"
) -> SetupClaimService:
    sequence = iter(digits * 10)
    return SetupClaimService(
        ClaimMode.LED,
        led,
        started_at=clock.now,
        clock=clock,
        choose_digit=lambda: next(sequence),
    )


def test_led_blink_schedule() -> None:
    assert blink_schedule("21") == [
        (1, 0.25),
        (0, 0.25),
        (1, 0.25),
        (0, 0.25),
        (0, 1.5),
        (1, 0.25),
        (0, 0.25),
        (0, 4.0),
    ]
    assert sum(1 for b, _ in blink_schedule("9999") if b == 1) == 36


def test_window_counts_down_from_start() -> None:
    clock = FakeClock()
    service = make_service(clock, FakeLed())
    assert service.window_seconds_left() == WINDOW_SECONDS
    clock.now += 60
    assert service.window_seconds_left() == WINDOW_SECONDS - 60
    clock.now += WINDOW_SECONDS
    assert service.window_seconds_left() == 0


async def test_issue_code_blinks_a_new_code() -> None:
    clock, led = FakeClock(), FakeLed()
    service = make_service(clock, led, digits="4321")

    assert await service.issue_code() == CODE_TTL_SECONDS
    assert led.shown == ["4321"]
    await service.verify("4321")


async def test_issue_code_limits() -> None:
    clock, led = FakeClock(), FakeLed()
    service = make_service(clock, led)
    await service.issue_code()
    with pytest.raises(TooSoonError):
        await service.issue_code()
    for _ in range(MAX_CODES_PER_WINDOW - 1):
        clock.now += CODE_INTERVAL_SECONDS
        await service.issue_code()
    clock.now += CODE_INTERVAL_SECONDS
    with pytest.raises(TooManyCodesError):
        await service.issue_code()
    assert len(led.shown) == MAX_CODES_PER_WINDOW


async def test_window_closes() -> None:
    clock, led = FakeClock(), FakeLed()
    service = make_service(clock, led)
    await service.issue_code()
    clock.now += WINDOW_SECONDS
    with pytest.raises(WindowClosedError):
        await service.issue_code()
    with pytest.raises(WindowClosedError):
        await service.verify("1234")


async def test_verify_rejects_missing_expired_and_wrong_codes() -> None:
    clock, led = FakeClock(), FakeLed()
    service = make_service(clock, led)
    with pytest.raises(InvalidCodeError):
        await service.verify("1234")

    await service.issue_code()
    with pytest.raises(InvalidCodeError):
        await service.verify(None)
    with pytest.raises(InvalidCodeError):
        await service.verify("9999")
    with pytest.raises(InvalidCodeError, match="new one"):
        await service.verify("9998")
    # Third wrong try spent the code: even the right one is refused now.
    with pytest.raises(InvalidCodeError):
        await service.verify("1234")
    assert led.stopped >= 1

    clock.now += CODE_INTERVAL_SECONDS
    await service.issue_code()
    clock.now += CODE_TTL_SECONDS
    with pytest.raises(InvalidCodeError):
        await service.verify("1234")


async def test_complete_clears_code_and_stops_led() -> None:
    clock, led = FakeClock(), FakeLed()
    service = make_service(clock, led)
    await service.issue_code()
    await service.complete()
    assert led.stopped == 1
    with pytest.raises(InvalidCodeError):
        await service.verify("1234")


async def test_setup_code_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    clock, led = FakeClock(), FakeLed()
    service = make_service(clock, led, digits="7531")
    with caplog.at_level(logging.DEBUG):
        await service.issue_code()
        with pytest.raises(InvalidCodeError):
            await service.verify("1111")
        await service.verify("7531")
    assert "7531" not in caplog.text


def _fake_led_dir(tmp_path: Path) -> Path:
    led = tmp_path / "ACT"
    led.mkdir()
    (led / "trigger").write_text("none [mmc0] timer heartbeat\n")
    (led / "brightness").write_text("0\n")
    return led


async def test_sysfs_led_blinks_and_restores_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    led_dir = _fake_led_dir(tmp_path)
    writes: list[str] = []
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        writes.append((led_dir / "brightness").read_text())
        await real_sleep(0)

    monkeypatch.setattr("app.services.setup_claim.asyncio.sleep", fast_sleep)
    led = SysfsLed(led_dir)

    await led.show("2")
    assert (led_dir / "trigger").read_text() == "none"
    for _ in range(20):
        await real_sleep(0)
    await led.stop()

    assert writes[:4] == ["1", "0", "1", "0"]
    assert (led_dir / "trigger").read_text() == "mmc0"


async def test_sysfs_led_without_led_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(LedUnavailableError):
        await SysfsLed(tmp_path / "missing").show("1234")


async def test_sysfs_led_stops_when_led_becomes_unwritable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    led_dir = _fake_led_dir(tmp_path)
    led = SysfsLed(led_dir)
    await led.show("1")
    (led_dir / "brightness").unlink()
    (led_dir / "brightness").mkdir()
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await asyncio.sleep(0.3)
    await led.stop()
    assert "not writable" in caplog.text


async def test_sysfs_led_trigger_without_brackets(tmp_path: Path) -> None:
    led_dir = _fake_led_dir(tmp_path)
    (led_dir / "trigger").write_text("none mmc0\n")
    led = SysfsLed(led_dir)
    await led.show("1")
    await led.stop()
    assert (led_dir / "trigger").read_text() == "none"
