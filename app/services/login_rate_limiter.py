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
# Bounds memory against a caller spraying distinct IP/email keys. Far above
# what a home deployment ever sees legitimately.
_DEFAULT_MAX_KEYS = 10_000
# When the cap is hit, trim down to this fraction of it so the O(n) sweep
# runs once per batch of new keys, not on every request.
_TRIM_TO_FRACTION = 0.9


class LoginRateLimiter:
    """Sliding-window limiter: at most `max_attempts` per key within the
    last `window_seconds`, keyed by caller-supplied string (IP + email).

    At most `max_keys` keys are tracked. Reaching the cap first drops keys
    with no attempts left in the window; if that is not enough, the least
    recently attempted keys are evicted. Eviction resets those keys' counters,
    which an attacker can only force by generating ~max_keys fresh keys,
    and only for keys that were already the least recently tried.
    """

    def __init__(
        self,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = _DEFAULT_MAX_KEYS,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._max_keys = max_keys
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
            # pop + reinsert keeps the dict ordered from least to most
            # recently attempted, which is what eviction relies on.
            recent_attempts = self._within_window(self._attempts.pop(key, []), now)
            allowed = len(recent_attempts) < self._max_attempts
            if allowed:
                recent_attempts.append(now)
            if len(self._attempts) >= self._max_keys:
                self._trim(now)
            self._attempts[key] = recent_attempts
            return allowed

    def _within_window(self, attempts: list[float], now: float) -> list[float]:
        cutoff = now - self._window_seconds
        return [attempt for attempt in attempts if attempt > cutoff]

    def _trim(self, now: float) -> None:
        """Drop expired keys, then evict the oldest until under the target."""
        self._attempts = {
            key: recent
            for key, attempts in self._attempts.items()
            if (recent := self._within_window(attempts, now))
        }
        target = int(self._max_keys * _TRIM_TO_FRACTION)
        for key in list(self._attempts)[: max(0, len(self._attempts) - target)]:
            del self._attempts[key]
