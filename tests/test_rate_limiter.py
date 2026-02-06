"""Tests for utils/rate_limiter.py."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.utils.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    RateLimitState,
    TokenBucket,
)


class TestTokenBucket:
    """Test suite for TokenBucket."""

    @pytest.mark.asyncio
    async def test_acquire_available_tokens(self) -> None:
        """Test acquiring available tokens immediately."""
        bucket = TokenBucket(tokens_per_second=10, max_tokens=10)
        wait_time = await bucket.acquire(1)
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_acquire_multiple_tokens(self) -> None:
        """Test acquiring multiple tokens."""
        bucket = TokenBucket(tokens_per_second=10, max_tokens=10)
        await bucket.acquire(5)
        assert bucket.available_tokens == 5.0

    @pytest.mark.asyncio
    async def test_acquire_waits_when_exhausted(self) -> None:
        """Test that acquire waits when tokens exhausted."""
        bucket = TokenBucket(tokens_per_second=100, max_tokens=1)
        await bucket.acquire(1)  # Exhaust tokens

        start = time.monotonic()
        await bucket.acquire(1)  # Should wait
        elapsed = time.monotonic() - start

        assert elapsed >= 0.009  # At least ~10ms for 1 token at 100/sec

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self) -> None:
        """Test that tokens refill over time."""
        bucket = TokenBucket(tokens_per_second=100, max_tokens=10)
        await bucket.acquire(10)  # Exhaust all tokens
        assert bucket.available_tokens == 0.0

        await asyncio.sleep(0.05)  # Wait 50ms for ~5 tokens to refill
        bucket._refill()
        assert bucket.available_tokens >= 4.0  # Allow some variance


class TestRateLimitState:
    """Test suite for RateLimitState."""

    def test_update_from_headers(self) -> None:
        """Test updating state from response headers."""
        state = RateLimitState()
        headers = {
            "X-MBX-USED-WEIGHT-1M": "500",
            "Retry-After": "30",
        }
        state.update_from_headers(headers)

        assert state.used_weight == 500
        assert state.retry_after == 30

    def test_should_backoff_below_threshold(self) -> None:
        """Test should_backoff returns False below threshold."""
        state = RateLimitState()
        state.used_weight = 1000  # 1000/2400 = ~42%
        assert state.should_backoff() is False

    def test_should_backoff_above_threshold(self) -> None:
        """Test should_backoff returns True above threshold."""
        state = RateLimitState()
        state.used_weight = 2000  # 2000/2400 = ~83%
        assert state.should_backoff() is True


class TestRateLimitConfig:
    """Test suite for RateLimitConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = RateLimitConfig()
        assert config.requests_per_minute == 1200
        assert config.max_retries == 3
        assert config.base_backoff_ms == 1000

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = RateLimitConfig(
            requests_per_minute=600,
            max_retries=5,
        )
        assert config.requests_per_minute == 600
        assert config.max_retries == 5


class TestRateLimiter:
    """Test suite for RateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_passes_through(self) -> None:
        """Test acquire allows requests when not rate limited."""
        config = RateLimitConfig(
            requests_per_minute=6000,  # High limit
            min_interval_ms=0,
        )
        limiter = RateLimiter(config)
        await limiter.acquire(weight=1)  # Should not block

    @pytest.mark.asyncio
    async def test_acquire_respects_min_interval(self) -> None:
        """Test acquire respects minimum interval."""
        config = RateLimitConfig(
            requests_per_minute=6000,
            min_interval_ms=50,
        )
        limiter = RateLimiter(config)

        await limiter.acquire(1)
        start = time.monotonic()
        await limiter.acquire(1)
        elapsed = (time.monotonic() - start) * 1000

        assert elapsed >= 45  # Allow some variance

    def test_update_from_response(self) -> None:
        """Test updating state from API response."""
        limiter = RateLimiter()
        headers = {"X-MBX-USED-WEIGHT-1M": "100"}
        limiter.update_from_response(headers)

        assert limiter._state.used_weight == 100
        assert limiter._consecutive_errors == 0

    def test_record_error_increments_count(self) -> None:
        """Test recording errors increments count."""
        limiter = RateLimiter()
        limiter.record_error(500)
        assert limiter._consecutive_errors == 1

        limiter.record_error(500)
        assert limiter._consecutive_errors == 2

    def test_get_backoff_time_exponential(self) -> None:
        """Test exponential backoff calculation."""
        config = RateLimitConfig(
            base_backoff_ms=1000,
            max_backoff_ms=30000,
        )
        limiter = RateLimiter(config)

        # No errors = no backoff
        assert limiter.get_backoff_time() == 0.0

        # First error = base backoff
        limiter._consecutive_errors = 1
        assert limiter.get_backoff_time() == 1.0

        # Second error = 2x backoff
        limiter._consecutive_errors = 2
        assert limiter.get_backoff_time() == 2.0

        # Third error = 4x backoff
        limiter._consecutive_errors = 3
        assert limiter.get_backoff_time() == 4.0

    def test_get_backoff_time_max_limit(self) -> None:
        """Test backoff time respects max limit."""
        config = RateLimitConfig(
            base_backoff_ms=1000,
            max_backoff_ms=5000,
        )
        limiter = RateLimiter(config)
        limiter._consecutive_errors = 10  # Would be 512 seconds

        assert limiter.get_backoff_time() == 5.0  # Capped at 5s

    @pytest.mark.asyncio
    async def test_wait_for_retry_allowed(self) -> None:
        """Test wait_for_retry allows retry within limit."""
        config = RateLimitConfig(
            max_retries=3,
            base_backoff_ms=10,  # Short for testing
        )
        limiter = RateLimiter(config)
        limiter._consecutive_errors = 1

        result = await limiter.wait_for_retry()
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_retry_exceeded(self) -> None:
        """Test wait_for_retry returns False when exceeded."""
        config = RateLimitConfig(max_retries=3)
        limiter = RateLimiter(config)
        limiter._consecutive_errors = 4  # Exceeded max retries

        result = await limiter.wait_for_retry()
        assert result is False

    def test_get_stats(self) -> None:
        """Test getting rate limiter statistics."""
        limiter = RateLimiter()
        limiter._state.used_weight = 500
        limiter._consecutive_errors = 2

        stats = limiter.get_stats()

        assert "requests_per_minute" in stats
        assert "available_tokens" in stats
        assert stats["used_weight"] == 500
        assert stats["consecutive_errors"] == 2
