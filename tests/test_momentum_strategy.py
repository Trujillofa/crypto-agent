import pytest

from src.strategy.momentum_strategy import MomentumStrategy
from src.strategy.signals import SignalType


class TestMomentumStrategy:
    @pytest.fixture
    def strategy(self):
        return MomentumStrategy(
            {
                "rsi_buy_threshold": 50.0,
                "rsi_sell_threshold": 50.0,
                "rsi_max_entry": 70.0,
                "rsi_min_entry": 30.0,
            }
        )

    @pytest.mark.asyncio
    async def test_warmup_none_value(self, strategy):
        indicators = {
            "rsi_14": None,
            "ema_50": 50000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "Waiting for RSI/EMA" in signal.reason

    @pytest.mark.asyncio
    async def test_missing_indicator(self, strategy):
        indicators = {"close_price": 50000.0}
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", indicators)

    @pytest.mark.asyncio
    async def test_neutral_hold(self, strategy):
        indicators = {
            "rsi_14": 50.0,
            "ema_50": 50000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "RSI: 50.00" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_signal(self, strategy):
        indicators = {
            "rsi_14": 55.0,
            "ema_50": 49000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.BUY
        assert signal.confidence > 0.5
        assert "Trend UP" in signal.reason
        assert "Momentum UP" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_signal_rsi_too_high(self, strategy):
        indicators = {
            "rsi_14": 75.0,
            "ema_50": 49000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "RSI too high" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_signal_weak_rsi(self, strategy):
        indicators = {
            "rsi_14": 45.0,
            "ema_50": 49000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "RSI weak" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_signal(self, strategy):
        indicators = {
            "rsi_14": 45.0,
            "ema_50": 51000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.SELL
        assert signal.confidence > 0.5
        assert "Trend DOWN" in signal.reason
        assert "Momentum DOWN" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_signal_rsi_too_low(self, strategy):
        indicators = {
            "rsi_14": 25.0,
            "ema_50": 51000.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "RSI too low" in signal.reason
