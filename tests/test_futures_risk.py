"""Tests for RiskManager futures risk methods."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.risk.manager import RiskManager, RiskConfig, FuturesLimits


class TestFuturesRiskMethods:
    """Test suite for futures-specific risk management."""

    @pytest.fixture
    def risk_manager(self):
        """Create a risk manager with default config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "risk_state.json"
            config_path = Path(tmpdir) / "risk.yaml"

            # Create minimal config file
            config_content = """
position_limits:
  max_position_pct: 0.10
  max_open_positions: 5

loss_limits:
  max_daily_loss_pct: 0.05
  max_drawdown_pct: 0.15
  max_single_loss_pct: 0.02

circuit_breakers:
  consecutive_losses: 5
  api_errors: 3
  latency_spike_ms: 5000

kill_switch:
  enabled: true
  telegram_confirm: false

futures_limits:
  max_leverage: 10
  liquidation_buffer_pct: 5.0
  max_daily_loss_pct: 5.0
  max_margin_usage_pct: 50.0
  margin_mode: isolated
  position_mode: one-way
"""
            config_path.write_text(config_content)

            rm = RiskManager(
                config_path=config_path,
                state_path=state_path,
            )
            yield rm

    def test_check_liquidation_buffer_long_safe(self, risk_manager):
        """Test liquidation buffer check for LONG position - safe distance."""
        # LONG: mark=50000, liq=45000, 5% buffer threshold = 47250
        # mark=50000 > 47250, so safe
        allowed, reason = risk_manager.check_liquidation_buffer(
            mark_price=50000.0,
            liquidation_price=45000.0,
            position_side="LONG",
            buffer_pct=5.0,
        )

        assert allowed is True
        assert reason == "OK"

    def test_check_liquidation_buffer_long_danger(self, risk_manager):
        """Test liquidation buffer check for LONG position - within buffer."""
        # LONG: mark=46000, liq=45000, 5% buffer threshold = 47250
        # mark=46000 < 47250, so danger
        allowed, reason = risk_manager.check_liquidation_buffer(
            mark_price=46000.0,
            liquidation_price=45000.0,
            position_side="LONG",
            buffer_pct=5.0,
        )

        assert allowed is False
        assert "within 5.0% of liquidation" in reason
        assert "LONG position" in reason

    def test_check_liquidation_buffer_short_safe(self, risk_manager):
        """Test liquidation buffer check for SHORT position - safe distance."""
        # SHORT: mark=50000, liq=55000, 5% buffer threshold = 52250
        # mark=50000 < 52250, so safe
        allowed, reason = risk_manager.check_liquidation_buffer(
            mark_price=50000.0,
            liquidation_price=55000.0,
            position_side="SHORT",
            buffer_pct=5.0,
        )

        assert allowed is True
        assert reason == "OK"

    def test_check_liquidation_buffer_short_danger(self, risk_manager):
        """Test liquidation buffer check for SHORT position - within buffer."""
        # SHORT: mark=54000, liq=55000, 5% buffer threshold = 52250
        # mark=54000 > 52250, so danger
        allowed, reason = risk_manager.check_liquidation_buffer(
            mark_price=54000.0,
            liquidation_price=55000.0,
            position_side="SHORT",
            buffer_pct=5.0,
        )

        assert allowed is False
        assert "within 5.0% of liquidation" in reason
        assert "SHORT position" in reason

    def test_check_liquidation_buffer_uses_config_default(self, risk_manager):
        """Test that config default buffer is used when not specified."""
        # Config has 5.0% default
        allowed, reason = risk_manager.check_liquidation_buffer(
            mark_price=46000.0,
            liquidation_price=45000.0,
            position_side="LONG",
            # No buffer_pct specified - should use config
        )

        # Should use config default (5.0%)
        assert allowed is False

    def test_check_max_leverage_within_limit(self, risk_manager):
        """Test leverage check within configured limit."""
        allowed, reason = risk_manager.check_max_leverage(5)

        assert allowed is True
        assert reason == "OK"

    def test_check_max_leverage_at_config_limit(self, risk_manager):
        """Test leverage at config limit (10x) is allowed."""
        allowed, reason = risk_manager.check_max_leverage(10)

        assert allowed is True
        assert reason == "OK"

    def test_check_max_leverage_exceeds_config_limit(self, risk_manager):
        """Test leverage exceeding config limit (10x) but below hard cap is rejected."""
        allowed, reason = risk_manager.check_max_leverage(20)

        assert allowed is False
        assert "exceeds configured max" in reason

    def test_check_max_leverage_exceeds_hard_cap(self, risk_manager):
        """Test leverage exceeding hard cap (20x) is rejected."""
        allowed, reason = risk_manager.check_max_leverage(50)

        assert allowed is False
        assert "exceeds hard safety cap of 20x" in reason

    def test_check_max_leverage_exceeds_configured_max(self, risk_manager):
        """Test leverage exceeding configured max (10x) is rejected."""
        # Config has max_leverage: 10
        allowed, reason = risk_manager.check_max_leverage(15)

        assert allowed is False
        assert "exceeds configured max 10x" in reason

    def test_check_margin_usage_within_limit(self, risk_manager):
        """Test margin usage within safe threshold."""
        # 30% usage (3000 / (3000 + 7000))
        allowed, reason = risk_manager.check_margin_usage(
            used_margin=3000.0, available_balance=7000.0
        )

        assert allowed is True
        assert "30.0%" in reason

    def test_check_margin_usage_exceeds_threshold(self, risk_manager):
        """Test margin usage exceeding safe threshold."""
        # 60% usage (6000 / (6000 + 4000))
        allowed, reason = risk_manager.check_margin_usage(
            used_margin=6000.0, available_balance=4000.0
        )

        assert allowed is False
        assert "60.0% exceeds safe threshold 50.0%" in reason

    def test_check_margin_usage_no_balance(self, risk_manager):
        """Test margin usage with no available balance."""
        allowed, reason = risk_manager.check_margin_usage(
            used_margin=1000.0, available_balance=0.0
        )

        assert allowed is False
        assert "No available balance" in reason

    def test_check_futures_daily_loss_within_limit(self, risk_manager):
        """Test futures daily loss within limit."""
        # -2% loss on 10000 account = 5% limit
        allowed, reason = risk_manager.check_futures_daily_loss(
            daily_pnl=-200.0, account_value=10000.0
        )

        assert allowed is True
        assert "daily PnL: -200.00" in reason

    def test_check_futures_daily_loss_exceeds_limit(self, risk_manager):
        """Test futures daily loss exceeding limit."""
        # -10% loss on 10000 account > 5% limit
        allowed, reason = risk_manager.check_futures_daily_loss(
            daily_pnl=-1000.0, account_value=10000.0
        )

        assert allowed is False
        assert "daily loss 10.00% exceeds limit 5.0%" in reason

    def test_check_futures_daily_loss_positive_pnl(self, risk_manager):
        """Test futures daily loss with positive PnL."""
        allowed, reason = risk_manager.check_futures_daily_loss(
            daily_pnl=500.0, account_value=10000.0
        )

        assert allowed is True

    def test_calculate_position_size_with_leverage_valid(self, risk_manager):
        """Test position size calculation with valid leverage."""
        allowed, margin_required, reason = (
            risk_manager.calculate_position_size_with_leverage(
                notional_value_usdt=10000.0, leverage=10, available_balance=2000.0
            )
        )

        assert allowed is True
        assert margin_required == 1000.0  # 10000 / 10
        assert "Margin required: 1000.00" in reason

    def test_calculate_position_size_with_leverage_insufficient_margin(
        self, risk_manager
    ):
        """Test position size calculation with insufficient margin."""
        allowed, margin_required, reason = (
            risk_manager.calculate_position_size_with_leverage(
                notional_value_usdt=10000.0,
                leverage=10,
                available_balance=500.0,  # Need 1000, have 500
            )
        )

        assert allowed is False
        assert margin_required == 1000.0
        assert "Insufficient margin" in reason

    def test_calculate_position_size_with_leverage_exceeds_limit(self, risk_manager):
        """Test position size calculation with leverage exceeding limit."""
        allowed, margin_required, reason = (
            risk_manager.calculate_position_size_with_leverage(
                notional_value_usdt=10000.0,
                leverage=50,  # Exceeds 20x hard cap
                available_balance=10000.0,
            )
        )

        assert allowed is False
        assert "exceeds hard safety cap" in reason

    def test_futures_limits_config_loaded(self, risk_manager):
        """Test that futures limits are loaded from config."""
        assert risk_manager._config.futures_limits.max_leverage == 10
        assert risk_manager._config.futures_limits.liquidation_buffer_pct == 5.0
        assert risk_manager._config.futures_limits.max_daily_loss_pct == 5.0
        assert risk_manager._config.futures_limits.max_margin_usage_pct == 50.0
        assert risk_manager._config.futures_limits.margin_mode == "isolated"
        assert risk_manager._config.futures_limits.position_mode == "one-way"
