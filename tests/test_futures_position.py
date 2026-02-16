"""Tests for Position model with futures support."""

from __future__ import annotations

import pytest

from src.portfolio.models import Position, PositionStatus


class TestPositionModel:
    """Test suite for Position model with futures support."""

    def test_spot_position_backward_compatible(self):
        """Test that spot positions work without new futures fields."""
        # Create a spot position (no futures fields)
        position = Position(
            id=1,
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
        )

        # All futures fields should have default values
        assert position.position_side is None  # Spot position
        assert position.leverage is None
        assert position.margin_type is None
        assert position.liquidation_price is None
        assert position.mark_price is None
        assert position.funding_fees == 0.0

        pnl = position.calculate_unrealized_pnl(51000.0)
        assert pnl == pytest.approx(89.9)

    def test_futures_long_position(self):
        """Test futures LONG position PnL calculation."""
        position = Position(
            id=1,
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,  # Positive for LONG
            position_side="LONG",
            leverage=10,
            margin_type="isolated",
        )

        # LONG position profit when price goes up
        pnl = position.calculate_unrealized_pnl(51000.0)
        assert pnl == pytest.approx(89.9)

        # LONG position loss when price goes down
        pnl = position.calculate_unrealized_pnl(49000.0)
        assert pnl == pytest.approx(-109.9)

    def test_futures_short_position(self):
        """Test futures SHORT position PnL calculation."""
        position = Position(
            id=1,
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,  # Positive quantity (SHORT indicated by position_side)
            position_side="SHORT",
            leverage=10,
            margin_type="isolated",
        )

        # SHORT position profit when price goes down
        pnl = position.calculate_unrealized_pnl(49000.0)
        assert pnl == pytest.approx(90.1)

        # SHORT position loss when price goes up
        pnl = position.calculate_unrealized_pnl(51000.0)
        assert pnl == pytest.approx(-110.1)

    def test_long_position_close(self):
        """Test closing a LONG position."""
        position = Position(
            id=1,
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
        )

        pnl = position.close(51000.0)

        assert pnl == pytest.approx(89.9)
        assert position.realized_pnl == pytest.approx(89.9)
        assert position.status == PositionStatus.CLOSED
        assert position.exit_price == 51000.0

    def test_short_position_close(self):
        """Test closing a SHORT position."""
        position = Position(
            id=1,
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="SHORT",
        )

        # Close SHORT at lower price = profit
        pnl = position.close(49000.0)

        assert pnl == pytest.approx(90.1)
        assert position.realized_pnl == pytest.approx(90.1)
        assert position.status == PositionStatus.CLOSED

    def test_spot_position_close_unchanged(self):
        """Test that spot position close calculation is unchanged."""
        position = Position(
            id=1,
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            # No position_side = spot
        )

        pnl = position.close(51000.0)

        assert pnl == pytest.approx(89.9)
        assert position.realized_pnl == pytest.approx(89.9)

    def test_calculate_liquidation_price_long(self):
        """Test liquidation price calculation for LONG."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
            leverage=10,
        )

        liq_price = position.calculate_liquidation_price(maintenance_margin_rate=0.005)

        # Formula: liq = entry * (1 - 1/leverage + maintenance)
        # liq = 50000 * (1 - 0.1 + 0.005) = 50000 * 0.905 = 45250
        expected = 50000.0 * (1 - 1 / 10 + 0.005)
        assert liq_price == pytest.approx(expected, rel=1e-10)
        assert position.liquidation_price == pytest.approx(expected, rel=1e-10)

    def test_calculate_liquidation_price_short(self):
        """Test liquidation price calculation for SHORT."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="SHORT",
            leverage=10,
        )

        liq_price = position.calculate_liquidation_price(maintenance_margin_rate=0.005)

        # Formula: liq = entry * (1 + 1/leverage - maintenance)
        # liq = 50000 * (1 + 0.1 - 0.005) = 50000 * 1.095 = 54750
        expected = 50000.0 * (1 + 1 / 10 - 0.005)
        assert liq_price == pytest.approx(expected, rel=1e-10)

    def test_liquidation_price_requires_futures_position(self):
        """Test that liquidation price requires a futures position."""
        # Spot position (no position_side)
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            # No position_side or leverage
        )

        with pytest.raises(ValueError) as exc_info:
            position.calculate_liquidation_price()

        assert "Leverage must be >= 1" in str(exc_info.value)

    def test_liquidation_price_requires_valid_leverage(self):
        """Test that liquidation price requires valid leverage."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
            leverage=0,  # Invalid
        )

        with pytest.raises(ValueError) as exc_info:
            position.calculate_liquidation_price()

        assert "Leverage must be >= 1" in str(exc_info.value)

    def test_update_mark_price(self):
        """Test updating mark price."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
        )

        pnl = position.update_mark_price(51000.0)

        assert position.mark_price == 51000.0
        assert pnl == pytest.approx(89.9)

    def test_is_near_liquidation_long(self):
        """Test liquidation buffer check for LONG."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
            leverage=10,
            liquidation_price=45000.0,
        )

        # Far from liquidation - safe
        position.mark_price = 48000.0
        assert position.is_near_liquidation(buffer_pct=5.0) is False

        # Within 5% of liquidation price
        position.mark_price = 45500.0  # 45000 * 1.05 = 47250, so this is safe
        # Actually: 45500 > 45000 * 1.05 = 47250? No, 45500 < 47250
        # So 45500 is actually within buffer (closer than 47250)
        # Wait: 45000 * 1.05 = 47250. 45500 < 47250, so we're closer than 5% buffer
        assert position.is_near_liquidation(buffer_pct=5.0) is True

        # At liquidation price
        position.mark_price = 45000.0
        assert position.is_near_liquidation(buffer_pct=5.0) is True

    def test_is_near_liquidation_short(self):
        """Test liquidation buffer check for SHORT."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="SHORT",
            leverage=10,
            liquidation_price=55000.0,
        )

        # Far from liquidation - safe
        position.mark_price = 52000.0
        assert position.is_near_liquidation(buffer_pct=5.0) is False

        # Within 5% of liquidation price
        # For SHORT: danger if mark >= liq * (1 - 0.05)
        # 55000 * 0.95 = 52250
        position.mark_price = 54000.0  # 54000 > 52250, so in danger zone
        assert position.is_near_liquidation(buffer_pct=5.0) is True

    def test_is_near_liquidation_no_liq_price(self):
        """Test that is_near_liquidation returns False without liquidation price."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
            # No liquidation_price set
        )

        position.mark_price = 1000.0  # Would be near liquidation if we had one
        assert position.is_near_liquidation() is False

    def test_funding_fees_accumulation(self):
        """Test funding fees tracking."""
        position = Position(
            symbol="BTCUSDT",
            entry_price=50000.0,
            quantity=0.1,
            position_side="LONG",
            funding_fees=10.0,  # Already paid $10 in funding
        )

        assert position.funding_fees == 10.0

        # Simulate adding more funding
        position.funding_fees += 5.0
        assert position.funding_fees == 15.0
