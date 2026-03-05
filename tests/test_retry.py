"""Tests for retry logic, circuit breaker, and dead letter queue."""

from __future__ import annotations

import asyncio
import pytest

from src.execution.retry import (
    CircuitBreaker,
    CircuitState,
    DeadLetterQueue,
    RetryConfig,
    retry_with_backoff,
)


# ---------------------------------------------------------------------------
# RetryConfig validation
# ---------------------------------------------------------------------------


class TestRetryConfig:
    def test_valid_config(self):
        config = RetryConfig(max_attempts=3)
        assert config.max_attempts == 3

    def test_max_attempts_zero_raises(self):
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryConfig(max_attempts=0)

    def test_max_attempts_negative_raises(self):
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryConfig(max_attempts=-1)

    def test_max_attempts_one_is_valid(self):
        config = RetryConfig(max_attempts=1)
        assert config.max_attempts == 1


# ---------------------------------------------------------------------------
# CircuitBreaker state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_closed_to_open_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_open_to_half_open_after_recovery_timeout(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate time passing beyond recovery_timeout
        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True

    def test_half_open_to_closed_after_enough_successes(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=2)
        cb.record_failure()

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        # Trigger HALF_OPEN transition
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        cb.record_failure()

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_reset_returns_to_closed(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_success_in_closed_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb._failure_count == 2

        cb.record_success()
        assert cb._failure_count == 0

    def test_half_open_limits_calls(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=2)
        cb.record_failure()

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True  # 0 half-open calls so far

        cb._half_open_calls = 2
        assert cb.can_execute() is False  # at limit


# ---------------------------------------------------------------------------
# retry_with_backoff
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    def test_succeeds_on_first_attempt(self):
        async def always_ok():
            return 42

        result = asyncio.get_event_loop().run_until_complete(
            retry_with_backoff(always_ok, RetryConfig(max_attempts=3))
        )
        assert result == 42

    def test_retries_and_succeeds(self):
        call_count = 0

        async def fails_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        config = RetryConfig(max_attempts=3, base_delay=0.0, jitter=0.0)
        result = asyncio.get_event_loop().run_until_complete(
            retry_with_backoff(fails_twice, config)
        )
        assert result == "ok"
        assert call_count == 3

    def test_raises_after_all_attempts_exhausted(self):
        async def always_fails():
            raise RuntimeError("boom")

        config = RetryConfig(max_attempts=3, base_delay=0.0, jitter=0.0)
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.get_event_loop().run_until_complete(retry_with_backoff(always_fails, config))

    def test_single_attempt_no_retry(self):
        call_count = 0

        async def fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("only once")

        config = RetryConfig(max_attempts=1, base_delay=0.0, jitter=0.0)
        with pytest.raises(ValueError):
            asyncio.get_event_loop().run_until_complete(retry_with_backoff(fails, config))
        assert call_count == 1


# ---------------------------------------------------------------------------
# DeadLetterQueue
# ---------------------------------------------------------------------------


class TestDeadLetterQueue:
    def test_add_and_retrieve(self):
        dlq = DeadLetterQueue()
        dlq.add("order1", "submit_order", (), {}, 3, "Exchange error")
        entries = dlq.get_all()
        assert len(entries) == 1
        assert entries[0].id == "order1"
        assert entries[0].last_error == "Exchange error"

    def test_max_size_trims_oldest(self):
        dlq = DeadLetterQueue(max_size=3)
        for i in range(4):
            dlq.add(f"order{i}", "fn", (), {}, 3, "err")

        entries = dlq.get_all()
        assert len(entries) == 3
        # Oldest (order0) should have been dropped
        assert entries[0].id == "order1"

    def test_clear(self):
        dlq = DeadLetterQueue()
        dlq.add("order1", "fn", (), {}, 3, "err")
        assert len(dlq) == 1

        dlq.clear()
        assert len(dlq) == 0

    def test_len(self):
        dlq = DeadLetterQueue()
        assert len(dlq) == 0
        dlq.add("a", "fn", (), {}, 1, "e")
        dlq.add("b", "fn", (), {}, 2, "e")
        assert len(dlq) == 2
