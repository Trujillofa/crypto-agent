"""Tests for risk/manager.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from src.risk.manager import (
    CircuitBreakers,
    KillSwitch,
    LossLimits,
    PositionLimits,
    RiskConfig,
    RiskManager,
)


class TestPositionLimits:
    """Test suite for PositionLimits dataclass."""

    def test_default_values(self) -> None:
        """Test default position limits."""
        limits = PositionLimits()
        assert limits.max_position_pct == 0.10
        assert limits.max_leverage == 3
        assert limits.max_open_positions == 5

    def test_custom_values(self) -> None:
        """Test custom position limits."""
        limits = PositionLimits(
            max_position_pct=0.20,
            max_leverage=5,
            max_open_positions=10,
        )
        assert limits.max_position_pct == 0.20
        assert limits.max_leverage == 5
        assert limits.max_open_positions == 10


class TestLossLimits:
    """Test suite for LossLimits dataclass."""

    def test_default_values(self) -> None:
        """Test default loss limits."""
        limits = LossLimits()
        assert limits.max_daily_loss_pct == 0.05
        assert limits.max_drawdown_pct == 0.15
        assert limits.max_single_loss_pct == 0.02

    def test_custom_values(self) -> None:
        """Test custom loss limits."""
        limits = LossLimits(
            max_daily_loss_pct=0.10,
            max_drawdown_pct=0.25,
            max_single_loss_pct=0.05,
        )
        assert limits.max_daily_loss_pct == 0.10
        assert limits.max_drawdown_pct == 0.25
        assert limits.max_single_loss_pct == 0.05


class TestCircuitBreakers:
    """Test suite for CircuitBreakers dataclass."""

    def test_default_values(self) -> None:
        """Test default circuit breaker values."""
        breakers = CircuitBreakers()
        assert breakers.consecutive_losses == 5
        assert breakers.api_errors == 3
        assert breakers.latency_spike_ms == 5000


class TestKillSwitch:
    """Test suite for KillSwitch dataclass."""

    def test_default_values(self) -> None:
        """Test default kill switch values."""
        switch = KillSwitch()
        assert switch.enabled is True
        assert switch.telegram_confirm is True


class TestRiskConfig:
    """Test suite for RiskConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default risk configuration."""
        config = RiskConfig()
        assert isinstance(config.position_limits, PositionLimits)
        assert isinstance(config.loss_limits, LossLimits)
        assert isinstance(config.circuit_breakers, CircuitBreakers)
        assert isinstance(config.kill_switch, KillSwitch)


class TestRiskManager:
    """Test suite for RiskManager."""

    def test_init_without_config(self) -> None:
        """Test initialization without config file uses defaults."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        assert manager._config is not None
        assert manager._kill_switch_triggered is False

    def test_init_with_config(self) -> None:
        """Test initialization with config file."""
        config = {
            "position_limits": {"max_position_pct": 0.20},
            "loss_limits": {"max_daily_loss_pct": 0.10},
            "circuit_breakers": {"consecutive_losses": 3},
            "kill_switch": {"enabled": False},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        manager = RiskManager(config_path=config_path)
        assert manager._config.position_limits.max_position_pct == 0.20
        assert manager._config.loss_limits.max_daily_loss_pct == 0.10
        assert manager._config.circuit_breakers.consecutive_losses == 3
        assert manager._config.kill_switch.enabled is False

        config_path.unlink()

    def test_is_trading_allowed_initial(self) -> None:
        """Test trading is allowed initially."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        allowed, reason = manager.is_trading_allowed()
        assert allowed is True
        assert reason == "Trading allowed"

    def test_is_trading_blocked_by_kill_switch(self) -> None:
        """Test trading blocked when kill switch is active."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._kill_switch_triggered = True
        allowed, reason = manager.is_trading_allowed()
        assert allowed is False
        assert "Kill switch" in reason

    def test_check_position_limit_allowed(self) -> None:
        """Test position limit check passes for valid position."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        allowed, reason = manager.check_position_limit(
            symbol="BTCUSDT",
            size=0.05,  # 5% of portfolio
            portfolio_value=10000,
        )
        assert allowed is True
        assert reason == "OK"

    def test_check_position_limit_exceeded(self) -> None:
        """Test position limit check fails for oversized position."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        allowed, reason = manager.check_position_limit(
            symbol="BTCUSDT",
            size=0.20,  # 20% of portfolio, exceeds 10% limit
            portfolio_value=10000,
        )
        assert allowed is False
        assert "exceeds max" in reason

    def test_check_position_limit_max_positions(self) -> None:
        """Test max open positions limit."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        # Fill up positions to max (5)
        for i in range(5):
            manager._positions[f"SYMBOL{i}"] = {"size": 0.01}

        # Try to add 6th position
        allowed, reason = manager.check_position_limit(
            symbol="NEWPAIR",
            size=0.05,
            portfolio_value=10000,
        )
        assert allowed is False
        assert "Max open positions" in reason

    def test_check_position_limit_kill_switch_blocks(self) -> None:
        """Test position checks blocked by kill switch."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._kill_switch_triggered = True
        allowed, reason = manager.check_position_limit(
            symbol="BTCUSDT",
            size=0.05,
            portfolio_value=10000,
        )
        assert allowed is False
        assert "Kill switch" in reason

    def test_record_trade_winning(self) -> None:
        """Test recording a winning trade."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._peak_balance = 10000
        manager.record_trade("BTCUSDT", pnl=100, portfolio_value=10000)
        assert manager._daily_pnl == 100
        assert manager._consecutive_losses == 0

    def test_record_trade_losing(self) -> None:
        """Test recording a losing trade."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._peak_balance = 10000
        manager.record_trade("BTCUSDT", pnl=-50, portfolio_value=10000)
        assert manager._daily_pnl == -50
        assert manager._consecutive_losses == 1

    def test_consecutive_losses_circuit_breaker(self) -> None:
        """Test consecutive losses triggers circuit breaker."""
        config = {
            "circuit_breakers": {"consecutive_losses": 3},
            "kill_switch": {"enabled": False},  # Disable for testing
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        manager = RiskManager(config_path=config_path)
        manager._peak_balance = 10000

        for i in range(3):
            manager.record_trade("BTCUSDT", pnl=-10, portfolio_value=10000)

        assert manager._circuit_breakers["consecutive_losses"] is True
        config_path.unlink()

    def test_record_api_error(self) -> None:
        """Test API error recording."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager.record_api_error()
        assert manager._api_error_count == 1

    def test_api_error_circuit_breaker(self) -> None:
        """Test API errors trigger circuit breaker."""
        config = {
            "circuit_breakers": {"api_errors": 2},
            "kill_switch": {"enabled": False},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        manager = RiskManager(config_path=config_path)
        manager.record_api_error()
        manager.record_api_error()

        assert manager._circuit_breakers["api_errors"] is True
        config_path.unlink()

    def test_record_latency(self) -> None:
        """Test latency recording."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager.record_latency(100)
        assert len(manager._latency_readings) == 1
        assert manager._latency_readings[0] == 100

    def test_latency_spike_circuit_breaker(self) -> None:
        """Test latency spike triggers circuit breaker."""
        config = {
            "circuit_breakers": {"latency_spike_ms": 1000},
            "kill_switch": {"enabled": False},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        manager = RiskManager(config_path=config_path)
        manager.record_latency(1500)  # Exceeds 1000ms threshold

        assert manager._circuit_breakers["latency"] is True
        config_path.unlink()

    def test_get_risk_summary(self) -> None:
        """Test risk summary generation."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._daily_pnl = 100
        manager._consecutive_losses = 2
        manager.record_latency(50)
        manager.record_latency(100)

        summary = manager.get_risk_summary()
        assert summary["daily_pnl"] == 100
        assert summary["consecutive_losses"] == 2
        assert summary["avg_latency_ms"] == 75.0
        assert summary["kill_switch_active"] is False

    def test_kill_switch_triggered_by_circuit_breaker(self) -> None:
        """Test kill switch is triggered by circuit breaker when enabled."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._peak_balance = 10000

        # Trigger consecutive losses
        for _ in range(5):
            manager.record_trade("BTCUSDT", pnl=-10, portfolio_value=10000)

        assert manager._kill_switch_triggered is True

    def test_daily_loss_circuit_breaker(self) -> None:
        """Test daily loss triggers circuit breaker."""
        config = {
            "loss_limits": {"max_daily_loss_pct": 0.05},
            "kill_switch": {"enabled": False},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        manager = RiskManager(config_path=config_path)
        manager._peak_balance = 10000
        # Record a loss exceeding 5% of portfolio
        manager.record_trade("BTCUSDT", pnl=-600, portfolio_value=10000)

        assert manager._circuit_breakers["daily_loss"] is True
        config_path.unlink()

    def test_circuit_breakers_block_trading(self) -> None:
        """Test active circuit breakers block trading."""
        manager = RiskManager(config_path=Path("/nonexistent/path.yaml"))
        manager._circuit_breakers["api_errors"] = True

        allowed, reason = manager.is_trading_allowed()
        assert allowed is False
        assert "api_errors" in reason
