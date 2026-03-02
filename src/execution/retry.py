"""Retry logic with exponential backoff and circuit breaker.

Provides:
- Retry decorator for async functions with configurable backoff
- Circuit breaker for exchange unavailability
- Dead letter queue for failed orders
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

from src.utils.logger import get_logger

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing if recovery


@dataclass
class CircuitBreaker:
    """Circuit breaker for exchange unavailability.

    States:
    - CLOSED: Normal operation, calls allowed
    - OPEN: Too many failures, calls rejected
    - HALF_OPEN: Testing recovery, limited calls allowed
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
_calls: int = field(default=0    _half_open, init=False)
    _logger: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._logger = get_logger(self.__class__.__name__)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._logger.info("Circuit breaker transitioning to HALF_OPEN")
        return self._state

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            return False
        # HALF_OPEN
        return self._half_open_calls < self.half_open_max_calls

    def record_success(self) -> None:
        """Record successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._logger.info("Circuit breaker recovered to CLOSED")
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record failed call."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._logger.warning("Circuit breaker reopened after HALF_OPEN failure")
        elif self._state == CircuitState.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._logger.warning(
                    "Circuit breaker opened after %d failures",
                    self._failure_count,
                )

    def reset(self) -> None:
        """Reset circuit breaker to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_calls = 0
        self._logger.info("Circuit breaker manually reset")

    def get_status(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "half_open_calls": self._half_open_calls,
            "last_failure": self._last_failure_time,
        }


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: float = 0.1


async def retry_with_backoff(
    func: Callable[..., T],
    config: RetryConfig | None = None,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Retry async function with exponential backoff.

    Args:
        func: Async function to retry
        config: Retry configuration
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result from func

    Raises:
        Last exception if all retries fail
    """
    config = config or RetryConfig()
    logger = get_logger("retry_with_backoff")
    last_exception: Exception | None = None

    for attempt in range(config.max_attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < config.max_attempts - 1:
                delay = min(
                    config.base_delay * (config.exponential_base**attempt),
                    config.max_delay,
                )
                # Add jitter
                import random

                delay *= 1 + random.uniform(-config.jitter, config.jitter)
                logger.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.2fs...",
                    attempt + 1,
                    config.max_attempts,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "All %d attempts failed: %s",
                    config.max_attempts,
                    e,
                )

    raise last_exception


@dataclass
class DeadLetterQueueEntry:
    """Entry in the dead letter queue."""

    id: str
    func_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    attempts: int
    last_error: str
    created_at: float = field(default_factory=time.time)


class DeadLetterQueue:
    """Queue for orders that failed after max retries."""

    def __init__(self, max_size: int = 100) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._queue: list[DeadLetterQueueEntry] = []
        self._max_size = max_size

    def add(
        self,
        entry_id: str,
        func_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        attempts: int,
        error: str,
    ) -> None:
        entry = DeadLetterQueueEntry(
            id=entry_id,
            func_name=func_name,
            args=args,
            kwargs=kwargs,
            attempts=attempts,
            last_error=error,
        )
        self._queue.append(entry)

        # Trim to max size
        if len(self._queue) > self._max_size:
            self._queue.pop(0)

        self._logger.error(
            "Order added to DLQ: %s after %d attempts (error: %s)",
            entry_id,
            attempts,
            error,
        )

    def get_all(self) -> list[DeadLetterQueueEntry]:
        return list(self._queue)

    def clear(self) -> None:
        self._queue.clear()
        self._logger.info("Dead letter queue cleared")

    def __len__(self) -> int:
        return len(self._queue)
