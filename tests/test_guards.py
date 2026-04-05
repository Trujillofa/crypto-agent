from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.risk.guards import (
    CircuitBreakerGuard,
    ConfidenceThresholdGuard,
    CooldownGuard,
    GuardContext,
    GuardPipeline,
    GuardResult,
    PositionLimitGuard,
)


def make_context(**overrides: object) -> GuardContext:
    context = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 1.0,
        "price": 100.0,
        "portfolio_value": 10_000.0,
        "current_position": 0.0,
        "signal_confidence": 0.75,
        "metadata": {"source": "test"},
    }
    context.update(overrides)
    return GuardContext(**context)


class TestGuardResult:
    def test_allow_factory_creates_passing_result(self) -> None:
        result = GuardResult.allow("ok", "TestGuard", {"checked": True})

        assert result.passed is True
        assert result.blocked is False
        assert result.reason == "ok"
        assert result.guard_name == "TestGuard"
        assert result.metadata == {"checked": True}

    def test_block_factory_creates_blocking_result(self) -> None:
        result = GuardResult.block("blocked", "TestGuard")

        assert result.passed is False
        assert result.blocked is True
        assert result.reason == "blocked"
        assert result.guard_name == "TestGuard"


class TestCircuitBreakerGuard:
    def test_pass_when_trading_allowed(self) -> None:
        risk_manager = Mock()
        risk_manager.is_trading_allowed.return_value = (True, "Trading enabled")
        guard = CircuitBreakerGuard(risk_manager)

        result = guard.check(make_context())

        assert result.passed is True
        assert result.reason == "Trading allowed"
        assert result.guard_name == "CircuitBreaker"

    def test_block_when_circuit_breaker_tripped(self) -> None:
        risk_manager = Mock()
        risk_manager.is_trading_allowed.return_value = (False, "Daily loss limit reached")
        guard = CircuitBreakerGuard(risk_manager)

        result = guard.check(make_context())

        assert result.blocked is True
        assert result.reason == "Daily loss limit reached"
        assert result.guard_name == "CircuitBreaker"


class TestPositionLimitGuard:
    def test_allow_when_buy_is_within_limit(self) -> None:
        guard = PositionLimitGuard(max_position_pct=0.10)

        result = guard.check(make_context(quantity=5.0, price=100.0, portfolio_value=10_000.0))

        assert result.passed is True
        assert result.reason == "Within position limits"
        assert result.guard_name == "PositionLimit"

    def test_block_when_buy_exceeds_limit(self) -> None:
        guard = PositionLimitGuard(max_position_pct=0.10)

        result = guard.check(make_context(quantity=15.0, price=100.0, portfolio_value=10_000.0))

        assert result.blocked is True
        assert result.guard_name == "PositionLimit"
        assert result.metadata == {"max_pct": 0.10, "new_pct": 0.15}
        assert "Position would exceed 10%" in result.reason

    def test_allow_sell_even_when_size_is_large(self) -> None:
        guard = PositionLimitGuard(max_position_pct=0.10)

        result = guard.check(make_context(side="SELL", quantity=100.0, current_position=100.0))

        assert result.passed is True
        assert result.reason == "Sell allowed"


class TestCooldownGuard:
    def test_first_order_passes_and_records_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = CooldownGuard(min_seconds=60.0)
        monkeypatch.setattr("time.time", lambda: 1_000.0)

        result = guard.check(make_context(symbol="BTCUSDT"))

        assert result.passed is True
        assert result.reason == "Cooldown passed"
        assert guard._last_order_time["BTCUSDT"] == 1_000.0

    def test_second_order_during_cooldown_is_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        guard = CooldownGuard(min_seconds=60.0)
        guard._last_order_time["BTCUSDT"] = 1_000.0
        monkeypatch.setattr("time.time", lambda: 1_030.0)

        result = guard.check(make_context(symbol="BTCUSDT"))

        assert result.blocked is True
        assert result.guard_name == "Cooldown"
        assert result.metadata == {"elapsed": 30.0, "required": 60.0}
        assert "Cooldown active" in result.reason


class TestConfidenceThresholdGuard:
    def test_pass_above_threshold(self) -> None:
        guard = ConfidenceThresholdGuard(min_confidence=0.50)

        result = guard.check(make_context(signal_confidence=0.75))

        assert result.passed is True
        assert result.reason == "Confidence sufficient"
        assert result.guard_name == "ConfidenceThreshold"

    def test_block_below_threshold(self) -> None:
        guard = ConfidenceThresholdGuard(min_confidence=0.80)

        result = guard.check(make_context(signal_confidence=0.60))

        assert result.blocked is True
        assert result.guard_name == "ConfidenceThreshold"
        assert result.metadata == {"confidence": 0.60, "min": 0.80}
        assert result.reason == "Confidence 0.60 < 0.80"


class TestGuardPipeline:
    def test_add_is_chainable_and_get_guard_names_returns_all_names(self) -> None:
        risk_manager = Mock()
        risk_manager.is_trading_allowed.return_value = (True, "ok")

        pipeline = GuardPipeline()
        returned = pipeline.add(CircuitBreakerGuard(risk_manager)).add(
            ConfidenceThresholdGuard(min_confidence=0.50)
        )

        assert returned is pipeline
        assert pipeline.get_guard_names() == ["CircuitBreaker", "ConfidenceThreshold"]

    def test_check_returns_allow_when_all_guards_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        risk_manager = Mock()
        risk_manager.is_trading_allowed.return_value = (True, "ok")
        monkeypatch.setattr("time.time", lambda: 1_000.0)
        pipeline = GuardPipeline(
            [
                CircuitBreakerGuard(risk_manager),
                PositionLimitGuard(max_position_pct=0.10),
                CooldownGuard(min_seconds=60.0),
                ConfidenceThresholdGuard(min_confidence=0.50),
            ]
        )

        result = pipeline.check(make_context(quantity=5.0, price=100.0, signal_confidence=0.9))

        assert result.passed is True
        assert result.reason == "All guards passed"
        assert result.guard_name == "Pipeline"
        assert result.metadata == {"guards_checked": 4}

    def test_check_short_circuits_on_first_blocking_guard(self) -> None:
        risk_manager = Mock()
        risk_manager.is_trading_allowed.return_value = (False, "Trading halted")
        pipeline = GuardPipeline(
            [
                CircuitBreakerGuard(risk_manager),
                ConfidenceThresholdGuard(min_confidence=0.95),
            ]
        )

        result = pipeline.check(make_context(signal_confidence=0.10))

        assert result.blocked is True
        assert result.guard_name == "CircuitBreaker"
        assert result.reason == "Trading halted"
