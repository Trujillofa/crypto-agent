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

    def test_explicit_notional_is_used_over_quantity_times_price(self) -> None:
        """The executors pass notional explicitly; it must win over qty*price.

        spot BUY passes quote notional as `quantity` (USDT) while `price` is a
        per-unit price, so `quantity * price` would inflate the notional by the
        price factor.
        """
        guard = PositionLimitGuard(max_position_pct=0.10)

        # 500 USDT notional on a 10k portfolio = 5% -> allowed.
        # quantity*price would be 500*100 = 50,000 -> 500% -> blocked.
        result = guard.check(
            make_context(
                quantity=500.0,
                price=100.0,
                portfolio_value=10_000.0,
                order_notional=500.0,
            )
        )

        assert result.passed is True
        assert result.reason == "Within position limits"

    def test_blocks_when_explicit_notional_exceeds_limit(self) -> None:
        guard = PositionLimitGuard(max_position_pct=0.10)

        result = guard.check(
            make_context(
                quantity=2_000.0,
                price=100.0,
                portfolio_value=10_000.0,
                order_notional=2_000.0,
            )
        )

        assert result.blocked is True
        assert result.metadata == {"max_pct": 0.10, "new_pct": 0.2}

    def test_fails_closed_when_portfolio_value_is_unknown(self) -> None:
        """A limit that cannot be evaluated must not silently allow the order."""
        guard = PositionLimitGuard(max_position_pct=0.10)

        result = guard.check(make_context(quantity=5.0, price=100.0, portfolio_value=0.0))

        assert result.blocked is True
        assert result.guard_name == "PositionLimit"
        assert "Portfolio value unavailable" in result.reason

    def test_fails_closed_when_notional_cannot_be_derived(self) -> None:
        guard = PositionLimitGuard(max_position_pct=0.10)

        result = guard.check(make_context(quantity=5.0, price=None, portfolio_value=10_000.0))

        assert result.blocked is True
        assert "Order notional unavailable" in result.reason

    def test_current_position_notional_is_added(self) -> None:
        guard = PositionLimitGuard(max_position_pct=0.10)

        # 400 existing + 700 new = 1100 / 10k = 11% -> blocked
        result = guard.check(
            make_context(
                quantity=700.0,
                price=1.0,
                portfolio_value=10_000.0,
                current_position=400.0,
            )
        )

        assert result.blocked is True
        assert result.metadata == {"max_pct": 0.10, "new_pct": 0.11}


class TestCooldownGuard:
    def test_first_order_passes_without_recording_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check() must NOT record the timestamp.

        The cooldown window has to start when the order FILLS, not when it is
        checked -- otherwise a rejected order still burns the window and
        blocks the next legitimate entry.
        """
        guard = CooldownGuard(min_seconds=60.0)
        monkeypatch.setattr("time.time", lambda: 1_000.0)

        result = guard.check(make_context(symbol="BTCUSDT"))

        assert result.passed is True
        assert result.reason == "Cooldown passed"
        assert "BTCUSDT" not in guard._last_order_time

    def test_record_order_starts_the_cooldown_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        guard = CooldownGuard(min_seconds=60.0)
        monkeypatch.setattr("time.time", lambda: 1_000.0)

        guard.record_order("BTCUSDT")

        assert guard._last_order_time["BTCUSDT"] == 1_000.0

    def test_rejected_order_does_not_burn_the_cooldown_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A guard check that never fills must not block the next entry."""
        guard = CooldownGuard(min_seconds=60.0)
        monkeypatch.setattr("time.time", lambda: 1_000.0)

        first = guard.check(make_context(symbol="BTCUSDT"))
        second = guard.check(make_context(symbol="BTCUSDT"))

        assert first.passed is True
        assert second.passed is True

    def test_sell_is_never_blocked_by_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A cooldown must not trap an open position."""
        guard = CooldownGuard(min_seconds=60.0)
        guard._last_order_time["BTCUSDT"] = 1_000.0
        monkeypatch.setattr("time.time", lambda: 1_030.0)

        result = guard.check(make_context(symbol="BTCUSDT", side="SELL"))

        assert result.passed is True
        assert result.guard_name == "Cooldown"

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
