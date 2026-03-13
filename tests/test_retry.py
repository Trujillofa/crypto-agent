"""Tests for retry logic, circuit breaker, and dead letter queue."""

from __future__ import annotations

import pytest

from src.execution.retry import (
    CircuitBreaker,
    CircuitState,
    DeadLetterQueue,
    RetryConfig,
    retry_with_backoff,
)


class TestRetryConfig:
    def test_valid_config(self) -> None:
        config = RetryConfig(max_attempts=3)
        assert config.max_attempts == 3

    def test_max_attempts_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryConfig(max_attempts=0)

    def test_max_attempts_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryConfig(max_attempts=-1)

    def test_max_attempts_one_is_valid(self) -> None:
        config = RetryConfig(max_attempts=1)
        assert config.max_attempts == 1


class TestCircuitBreaker:
    def test_initial_state_is_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_closed_to_open_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_open_to_half_open_after_recovery_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True

    def test_half_open_to_closed_after_enough_successes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=2)
        cb.record_failure()

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        cb.record_failure()

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_reset_returns_to_closed(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_success_in_closed_resets_failure_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb._failure_count == 2

        cb.record_success()
        assert cb._failure_count == 0

    def test_half_open_limits_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=2)
        cb.record_failure()

        monkeypatch.setattr(
            "src.execution.retry.time.monotonic",
            lambda: cb._last_failure_time + 31.0,
        )
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True

        cb._half_open_calls = 2
        assert cb.can_execute() is False


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self) -> None:
        async def always_ok() -> int:
            return 42

        result = await retry_with_backoff(always_ok, RetryConfig(max_attempts=3))
        assert result == 42

    @pytest.mark.asyncio
    async def test_retries_and_succeeds(self) -> None:
        call_count = 0

        async def fails_twice() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        config = RetryConfig(max_attempts=3, base_delay=0.0, jitter=0.0)
        result = await retry_with_backoff(fails_twice, config)
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_all_attempts_exhausted(self) -> None:
        async def always_fails() -> None:
            raise RuntimeError("boom")

        config = RetryConfig(max_attempts=3, base_delay=0.0, jitter=0.0)
        with pytest.raises(RuntimeError, match="boom"):
            await retry_with_backoff(always_fails, config)

    @pytest.mark.asyncio
    async def test_single_attempt_no_retry(self) -> None:
        call_count = 0

        async def fails() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("only once")

        config = RetryConfig(max_attempts=1, base_delay=0.0, jitter=0.0)
        with pytest.raises(ValueError):
            await retry_with_backoff(fails, config)
        assert call_count == 1


class TestDeadLetterQueue:
    def test_add_and_retrieve(self) -> None:
        dlq = DeadLetterQueue()
        dlq.add("order1", "submit_order", (), {}, 3, "Exchange error")
        entries = dlq.get_all()
        assert len(entries) == 1
        assert entries[0].id == "order1"
        assert entries[0].last_error == "Exchange error"

    def test_max_size_trims_oldest(self) -> None:
        dlq = DeadLetterQueue(max_size=3)
        for i in range(4):
            dlq.add(f"order{i}", "fn", (), {}, 3, "err")

        entries = dlq.get_all()
        assert len(entries) == 3
        assert entries[0].id == "order1"

    def test_clear(self) -> None:
        dlq = DeadLetterQueue()
        dlq.add("order1", "fn", (), {}, 3, "err")
        assert len(dlq) == 1

        dlq.clear()
        assert len(dlq) == 0

    def test_len(self) -> None:
        dlq = DeadLetterQueue()
        assert len(dlq) == 0
        dlq.add("a", "fn", (), {}, 1, "e")
        dlq.add("b", "fn", (), {}, 2, "e")
        assert len(dlq) == 2
