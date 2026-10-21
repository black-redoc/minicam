"""Tests for LoginRateLimiter: an in-memory sliding-window limiter driven by
an injected clock, never real time.sleep.
"""

from __future__ import annotations

from app.services.login_rate_limiter import LoginRateLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestMakeKey:
    def test_normalizes_email_case_and_whitespace(self) -> None:
        key_a = LoginRateLimiter.make_key("1.2.3.4", "User@Example.com")
        key_b = LoginRateLimiter.make_key("1.2.3.4", "  user@example.com  ")
        assert key_a == key_b

    def test_different_ips_produce_different_keys(self) -> None:
        key_a = LoginRateLimiter.make_key("1.2.3.4", "user@example.com")
        key_b = LoginRateLimiter.make_key("5.6.7.8", "user@example.com")
        assert key_a != key_b


class TestIsAllowed:
    async def test_allows_attempts_within_the_limit(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, clock=clock)

        assert await limiter.is_allowed("k") is True
        assert await limiter.is_allowed("k") is True
        assert await limiter.is_allowed("k") is True

    async def test_blocks_once_the_limit_is_exceeded(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, clock=clock)

        assert await limiter.is_allowed("k") is True
        assert await limiter.is_allowed("k") is True
        assert await limiter.is_allowed("k") is False

    async def test_different_keys_have_independent_limits(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(max_attempts=1, window_seconds=60, clock=clock)

        assert await limiter.is_allowed("k1") is True
        assert await limiter.is_allowed("k2") is True
        assert await limiter.is_allowed("k1") is False

    async def test_window_expiry_allows_attempts_again(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(max_attempts=1, window_seconds=60, clock=clock)

        assert await limiter.is_allowed("k") is True
        assert await limiter.is_allowed("k") is False

        clock.advance(61)

        assert await limiter.is_allowed("k") is True

    async def test_blocked_attempts_do_not_consume_a_future_window_slot(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(max_attempts=1, window_seconds=60, clock=clock)

        assert await limiter.is_allowed("k") is True
        for _ in range(5):
            assert await limiter.is_allowed("k") is False

        clock.advance(61)

        assert await limiter.is_allowed("k") is True


class TestMemoryBound:
    async def test_tracked_keys_never_exceed_max_keys(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(
            max_attempts=5, window_seconds=60, clock=clock, max_keys=10
        )

        for i in range(100):
            await limiter.is_allowed(f"k{i}")

        assert len(limiter._attempts) <= 10

    async def test_expired_keys_are_dropped_before_live_ones(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(
            max_attempts=1, window_seconds=60, clock=clock, max_keys=10
        )
        await limiter.is_allowed("victim")
        clock.advance(30)
        for i in range(8):
            await limiter.is_allowed(f"old{i}")
        clock.advance(31)  # old* still live, but "victim"'s attempt expired

        await limiter.is_allowed("v")
        await limiter.is_allowed("w")

        assert "victim" not in limiter._attempts
        assert all(f"old{i}" in limiter._attempts for i in range(8))

    async def test_evicts_least_recently_attempted_key_first(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(
            max_attempts=1, window_seconds=60, clock=clock, max_keys=10
        )
        for i in range(10):
            await limiter.is_allowed(f"k{i}")
        # Re-attempting k0 makes it the most recent, so k1 is now the oldest.
        assert await limiter.is_allowed("k0") is False

        await limiter.is_allowed("new")

        assert "k1" not in limiter._attempts
        assert await limiter.is_allowed("k0") is False

    async def test_blocked_key_stays_blocked_while_under_the_cap(self) -> None:
        clock = _FakeClock()
        limiter = LoginRateLimiter(
            max_attempts=1, window_seconds=60, clock=clock, max_keys=10
        )
        await limiter.is_allowed("attacker")
        for i in range(5):
            await limiter.is_allowed(f"other{i}")

        assert await limiter.is_allowed("attacker") is False
