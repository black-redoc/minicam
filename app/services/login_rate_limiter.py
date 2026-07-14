"""In-memory login attempt limiter.

A single uvicorn process on a Raspberry Pi has no other workers to
coordinate with, so a per-process, in-memory limiter is enough — no Redis,
no new dependency. Protected by `asyncio.Lock` (not a thread lock) because
callers run inside the same event loop as every request handler.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_WINDOW_SECONDS = 15 * 60


class LoginRateLimiter:
    """Sliding-window limiter: at most `max_attempts` per key within the
    last `window_seconds`, keyed by caller-supplied string (IP + email).
    """

    def __init__(
        self,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock
        self._attempts: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def make_key(client_ip: str, email: str) -> str:
        return f"{client_ip}:{email.strip().lower()}"

    async def is_allowed(self, key: str) -> bool:
        """Record one attempt for `key` and report whether it is allowed.

        Every call counts as an attempt (success or failure alike), matching
        the login route's requirement to check this before touching the
        database at all.
        """
        async with self._lock:
            now = self._clock()
            recent_attempts = self._attempts_within_window(key, now)
            allowed = len(recent_attempts) < self._max_attempts
            if allowed:
                recent_attempts.append(now)
            self._attempts[key] = recent_attempts
            return allowed

    def _attempts_within_window(self, key: str, now: float) -> list[float]:
        cutoff = now - self._window_seconds
        return [attempt for attempt in self._attempts.get(key, []) if attempt > cutoff]
