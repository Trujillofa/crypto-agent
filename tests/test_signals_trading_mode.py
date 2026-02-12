"""Tests for Signal trading_mode field."""

from __future__ import annotations

import pytest

from src.strategy.signals import Signal, SignalType


class TestSignalTradingMode:
    """Test suite for Signal trading_mode field."""

    def test_signal_default_trading_mode_spot(self):
        """Test that Signal defaults to spot trading mode."""
        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="EMA crossover",
            indicators={"ema_12": 49500.0, "ema_26": 49000.0},
        )

        assert signal.trading_mode == "spot"
        assert "spot" in str(signal)

    def test_signal_futures_trading_mode(self):
        """Test creating a futures trading signal."""
        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="EMA crossover",
            indicators={"ema_12": 49500.0, "ema_26": 49000.0},
            trading_mode="futures",
        )

        assert signal.trading_mode == "futures"
        assert "futures" in str(signal)

    def test_signal_immutable_frozen(self):
        """Test that Signal is frozen (immutable)."""
        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="Test",
            indicators={},
            trading_mode="futures",
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            signal.trading_mode = "spot"

    def test_signal_all_types_with_trading_mode(self):
        """Test all signal types work with trading_mode."""
        for signal_type in [SignalType.BUY, SignalType.SELL, SignalType.HOLD]:
            for mode in ["spot", "futures"]:
                signal = Signal(
                    type=signal_type,
                    symbol="BTCUSDT",
                    price=50000.0,
                    confidence=0.8,
                    reason="Test",
                    indicators={},
                    trading_mode=mode,
                )

                assert signal.type == signal_type
                assert signal.trading_mode == mode

    def test_signal_string_representation(self):
        """Test Signal string representation includes trading_mode."""
        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="EMA crossover",
            indicators={},
            trading_mode="futures",
        )

        signal_str = str(signal)
        assert "BUY" in signal_str
        assert "BTCUSDT" in signal_str
        assert "50000.00" in signal_str
        assert "futures" in signal_str
        assert "EMA crossover" in signal_str

    def test_backward_compatibility_existing_signals(self):
        """Test that existing spot signals work without trading_mode parameter."""
        # This simulates how existing strategies create signals
        signal = Signal(
            type=SignalType.SELL,
            symbol="ETHUSDT",
            price=3000.0,
            confidence=0.75,
            reason="RSI overbought",
            indicators={"rsi": 75.0},
            # No trading_mode specified - should default to "spot"
        )

        assert signal.trading_mode == "spot"
        assert signal.type == SignalType.SELL
