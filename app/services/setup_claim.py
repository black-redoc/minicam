"""First-admin setup claimed with a code blinked by the Pi's ACT LED (ADR 0006).

With MINICAM_SETUP_CLAIM=led (set by the Raspberry Pi OS image) the first
admin can only be created from the LAN, within WINDOW_SECONDS of the service
starting, by someone who can see the device: a LAN client asks for a code,
the LED blinks it, and POST /api/auth/setup must carry it.

The code is never logged or returned by any endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 30 * 60
CODE_TTL_SECONDS = 5 * 60
CODE_INTERVAL_SECONDS = 30
MAX_CODES_PER_WINDOW = 5
MAX_ATTEMPTS_PER_CODE = 3
CODE_DIGITS = 4

BLINK_ON = 0.25
BLINK_OFF = 0.25
DIGIT_PAUSE = 1.5
REPEAT_PAUSE = 4.0

# Imported when the app starts, so the window starts with the service, not
# with the first request.
PROCESS_STARTED_AT = time.monotonic()


class ClaimMode(StrEnum):
    NONE = "none"
    LED = "led"


class ClaimError(Exception):
    """A refused claim; the message is safe to show to the client."""


class WindowClosedError(ClaimError):
    pass


class TooSoonError(ClaimError):
    pass


class TooManyCodesError(ClaimError):
    pass


class InvalidCodeError(ClaimError):
    pass


class LedUnavailableError(ClaimError):
    pass


def blink_schedule(code: str) -> list[tuple[int, float]]:
    """(brightness, seconds) steps that blink code once, ending with the repeat pause.

    Each digit N is N short blinks; digits are separated by a longer pause.
    """
    steps: list[tuple[int, float]] = []
    for index, digit in enumerate(code):
        for _ in range(int(digit)):
            steps += [(1, BLINK_ON), (0, BLINK_OFF)]
        last = index == len(code) - 1
        steps.append((0, REPEAT_PAUSE if last else DIGIT_PAUSE))
    return steps


class LedSignaller(Protocol):
    async def show(self, code: str) -> None:
        """Start blinking code in a loop (returns at once)."""

    async def stop(self) -> None:
        """Stop blinking and give the LED back to its usual trigger."""


class SysfsLed:
    """Blinks through /sys/class/leds/<name>: trigger and brightness only."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._task: asyncio.Task[None] | None = None
        self._original_trigger: str | None = None

    def _current_trigger(self) -> str:
        # "none [mmc0] timer ...": the active trigger is the bracketed one.
        for word in (self._path / "trigger").read_text().split():
            if word.startswith("[") and word.endswith("]"):
                return word[1:-1]
        return "none"

    def _write(self, name: str, value: str) -> None:
        (self._path / name).write_text(value)

    async def show(self, code: str) -> None:
        await self.stop()
        try:
            self._original_trigger = self._current_trigger()
            self._write("trigger", "none")
            self._write("brightness", "0")
        except OSError as exc:
            raise LedUnavailableError("The device's LED can't be controlled") from exc
        self._task = asyncio.create_task(self._blink(code))

    async def _blink(self, code: str) -> None:
        schedule = blink_schedule(code)
        try:
            while True:
                for brightness, seconds in schedule:
                    self._write("brightness", str(brightness))
                    await asyncio.sleep(seconds)
        except OSError:
            logger.warning("Stopped blinking the setup code: the LED is not writable")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._original_trigger is not None:
            with contextlib.suppress(OSError):
                self._write("trigger", self._original_trigger)
            self._original_trigger = None


class SetupClaimService:
    def __init__(
        self,
        mode: ClaimMode,
        led: LedSignaller,
        started_at: float = PROCESS_STARTED_AT,
        clock: Callable[[], float] = time.monotonic,
        choose_digit: Callable[[], str] = lambda: secrets.choice("123456789"),
    ) -> None:
        self.mode = mode
        self._led = led
        self._started_at = started_at
        self._clock = clock
        self._choose_digit = choose_digit
        self._code: str | None = None
        self._code_expires_at = 0.0
        self._attempts_left = 0
        self._codes_issued = 0
        self._last_issued_at: float | None = None
        self._lock = asyncio.Lock()

    def window_seconds_left(self) -> int:
        """Seconds until setup closes, counted from the service start."""
        elapsed = self._clock() - self._started_at
        return max(0, int(WINDOW_SECONDS - elapsed))

    async def issue_code(self) -> int:
        """Create a new code, start blinking it, and return its lifetime in seconds.

        The caller has already checked that no user exists and the request is
        from the LAN.
        """
        async with self._lock:
            now = self._clock()
            if self.window_seconds_left() == 0:
                raise WindowClosedError(
                    "Setup is closed. Restart the device to set it up."
                )
            if (
                self._last_issued_at is not None
                and now - self._last_issued_at < CODE_INTERVAL_SECONDS
            ):
                raise TooSoonError("Wait a few seconds before asking for a new code.")
            if self._codes_issued >= MAX_CODES_PER_WINDOW:
                raise TooManyCodesError(
                    "Too many codes. Restart the device to try again."
                )
            code = "".join(self._choose_digit() for _ in range(CODE_DIGITS))
            await self._led.show(code)
            self._code = code
            self._code_expires_at = now + CODE_TTL_SECONDS
            self._attempts_left = MAX_ATTEMPTS_PER_CODE
            self._codes_issued += 1
            self._last_issued_at = now
            logger.info(
                "Setup code issued (%d of %d)", self._codes_issued, MAX_CODES_PER_WINDOW
            )
            return CODE_TTL_SECONDS

    async def verify(self, code: str | None) -> None:
        """Accept the blinked code, or raise; each wrong try uses one attempt."""
        async with self._lock:
            if self.window_seconds_left() == 0:
                raise WindowClosedError(
                    "Setup is closed. Restart the device to set it up."
                )
            current = self._code
            if current is None or self._clock() >= self._code_expires_at:
                await self._clear()
                raise InvalidCodeError("Ask the device for a new code.")
            if code is not None and hmac.compare_digest(
                code.encode(), current.encode()
            ):
                return
            self._attempts_left -= 1
            if self._attempts_left <= 0:
                await self._clear()
                raise InvalidCodeError("Wrong code. Ask the device for a new one.")
            raise InvalidCodeError("Wrong code. Count the blinks again.")

    async def complete(self) -> None:
        """The admin exists: forget the code and give the LED back."""
        async with self._lock:
            await self._clear()

    async def _clear(self) -> None:
        self._code = None
        self._attempts_left = 0
        await self._led.stop()
