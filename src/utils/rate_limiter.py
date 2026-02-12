"""Rate limiting utilities for API calls.

Implements token bucket algorithm with exponential backoff for retries.

Binance Futures API limits (as of 2024):
- 2400 request weight per minute
- Most endpoints cost 1-5 weight
- Klines endpoint costs 5 weight for limit=500, 1 for limit <= 100
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from src.utils.logger import get_logger


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""

    requests_per_minute: int = 1200  # Conservative limit (half of max)
    weight_per_minute: int = 1200  # Conservative weight limit
    burst_size: int = 10  # Max burst requests
    min_interval_ms: int = 50  # Minimum ms between requests
    max_retries: int = 3
    base_backoff_ms: int = 1000
    max_backoff_ms: int = 30000


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(
        self,
        tokens_per_second: float,
        max_tokens: int,
    ) -> None:
        self._tokens_per_second = tokens_per_second
        self._max_tokens = max_tokens
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> float:
        """Acquire tokens, waiting if necessary.

        Returns:
            Time waited in seconds
        """
        async with self._lock:
            self._refill()

            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0

            # Calculate wait time
            tokens_needed = tokens - self._tokens
            wait_time = tokens_needed / self._tokens_per_second

            await asyncio.sleep(wait_time)

            self._refill()
            self._tokens -= tokens
            return wait_time

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens,
            self._tokens + elapsed * self._tokens_per_second,
        )
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        """Get current available tokens."""
        return self._tokens


@dataclass
class RateLimitState:
    """Track rate limit state from API responses."""

    used_weight: int = 0
    limit_weight: int = 2400
    retry_after: int = 0
    last_update: float = field(default_factory=time.monotonic)

    def update_from_headers(self, headers: dict[str, str]) -> None:
        """Update state from Binance response headers."""
        if "X-MBX-USED-WEIGHT-1M" in headers:
            self.used_weight = int(headers["X-MBX-USED-WEIGHT-1M"])
        if "Retry-After" in headers:
            self.retry_after = int(headers["Retry-After"])
        self.last_update = time.monotonic()

    def should_backoff(self) -> bool:
        """Check if we should back off based on used weight."""
        return self.used_weight > self.limit_weight * 0.8


class RateLimiter:
    """Rate limiter with exponential backoff for Binance API."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._logger = get_logger(self.__class__.__name__)

        # Token bucket for request rate
        self._bucket = TokenBucket(
            tokens_per_second=self._config.requests_per_minute / 60.0,
            max_tokens=self._config.burst_size,
        )

        # Track recent requests for sliding window
        self._request_times: deque[float] = deque(maxlen=1000)
        self._state = RateLimitState()
        self._consecutive_errors = 0

    async def acquire(self, weight: int = 1) -> None:
        """Acquire permission to make a request.

        Args:
            weight: API endpoint weight (1-20)
        """
        # Check if we need to back off based on API response
        if self._state.should_backoff():
            backoff_time = 5.0  # Wait 5 seconds if nearing limit
            self._logger.warning(
                f"Rate limit at {self._state.used_weight}/{self._state.limit_weight}, "
                f"backing off for {backoff_time}s"
            )
            await asyncio.sleep(backoff_time)

        # Wait for token bucket
        wait_time = await self._bucket.acquire(weight)
        if wait_time > 0:
            self._logger.debug(f"Rate limited, waited {wait_time:.2f}s")

        # Enforce minimum interval
        await self._enforce_min_interval()

        # Record request time
        self._request_times.append(time.monotonic())

    async def _enforce_min_interval(self) -> None:
        """Enforce minimum interval between requests."""
        if not self._request_times:
            return

        elapsed_ms = (time.monotonic() - self._request_times[-1]) * 1000
        if elapsed_ms < self._config.min_interval_ms:
            wait_ms = self._config.min_interval_ms - elapsed_ms
            await asyncio.sleep(wait_ms / 1000)

    def update_from_response(self, headers: dict[str, str]) -> None:
        """Update rate limit state from API response headers."""
        self._state.update_from_headers(headers)
        self._consecutive_errors = 0

    def record_error(self, status_code: int) -> None:
        """Record an API error for backoff calculation."""
        self._consecutive_errors += 1

        if status_code == 429:  # Rate limit exceeded
            self._logger.warning("Rate limit exceeded (429)")
        elif status_code == 418:  # IP banned
            self._logger.error("IP banned (418) - backing off significantly")

    def get_backoff_time(self) -> float:
        """Calculate exponential backoff time in seconds."""
        if self._consecutive_errors == 0:
            return 0.0

        backoff_ms = min(
            self._config.base_backoff_ms * (2 ** (self._consecutive_errors - 1)),
            self._config.max_backoff_ms,
        )
        return backoff_ms / 1000

    async def wait_for_retry(self) -> bool:
        """Wait before retry with exponential backoff.

        Returns:
            True if retry is allowed, False if max retries exceeded
        """
        if self._consecutive_errors > self._config.max_retries:
            self._logger.error(f"Max retries ({self._config.max_retries}) exceeded")
            return False

        backoff_time = self.get_backoff_time()
        if backoff_time > 0:
            self._logger.info(
                f"Retry {self._consecutive_errors}/{self._config.max_retries}, "
                f"waiting {backoff_time:.2f}s"
            )
            await asyncio.sleep(backoff_time)

        return True

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics."""
        now = time.monotonic()
        recent_requests = sum(1 for t in self._request_times if now - t < 60)
        return {
            "requests_per_minute": recent_requests,
            "available_tokens": self._bucket.available_tokens,
            "used_weight": self._state.used_weight,
            "consecutive_errors": self._consecutive_errors,
        }
