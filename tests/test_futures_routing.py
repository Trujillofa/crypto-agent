import pytest
from src.strategy.aggregator import SignalAggregator
from src.strategy.signals import Signal, SignalType

def test_aggregator_trading_mode_spot():
    """Test that aggregator defaults to spot trading mode."""
    aggregator = SignalAggregator(default_trading_mode="spot")
    # aggregate(symbol, signals)
    signal = aggregator.aggregate("BTCUSDT", [])
    assert signal.trading_mode == "spot"

def test_aggregator_trading_mode_futures():
    """Test that aggregator correctly sets futures trading mode."""
    aggregator = SignalAggregator(default_trading_mode="futures")
    signal = aggregator.aggregate("BTCUSDT", [])
    assert signal.trading_mode == "futures"

def test_aggregator_hold_signal_trading_mode():
    """Test that aggregator hold signals also have the correct trading mode."""
    aggregator = SignalAggregator(default_trading_mode="futures")
    # Empty list of sub-signals results in HOLD
    signal = aggregator.aggregate("BTCUSDT", [])
    assert signal.type == SignalType.HOLD
    assert signal.trading_mode == "futures"
