import pytest
from src.strategy.bollinger_strategy import BollingerBounceStrategy
from src.strategy.signals import SignalType


class TestBollingerBounceStrategy:
    @pytest.fixture
    def strategy(self):
        return BollingerBounceStrategy(
            {
                "band_distance_threshold": 0.0,
                "rsi_oversold": 30.0,
                "rsi_overbought": 70.0,
            }
        )

    @pytest.mark.asyncio
    async def test_warmup_none_value(self, strategy):
        indicators = {
            "bb_upper_dist": None,
            "bb_lower_dist": 0.05,
            "rsi_14": 50.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "Waiting for Bollinger" in signal.reason

    @pytest.mark.asyncio
    async def test_missing_indicator(self, strategy):
        indicators = {"close_price": 50000.0}
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", indicators)

    @pytest.mark.asyncio
    async def test_neutral_hold(self, strategy):
        indicators = {
            "bb_upper_dist": 0.05,
            "bb_lower_dist": 0.05,
            "rsi_14": 50.0,
            "close_price": 50000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "BB Dist" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_signal(self, strategy):
        indicators = {
            "bb_upper_dist": 0.10,
            "bb_lower_dist": -0.001,
            "rsi_14": 25.0,
            "close_price": 40000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.BUY
        assert signal.confidence == 1.0
        assert "Price at Lower Band" in signal.reason
        assert "RSI Oversold" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_signal_filtered_rsi(self, strategy):
        indicators = {
            "bb_upper_dist": 0.10,
            "bb_lower_dist": -0.001,
            "rsi_14": 35.0,
            "close_price": 40000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "RSI not oversold" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_signal(self, strategy):
        indicators = {
            "bb_upper_dist": -0.001,
            "bb_lower_dist": 0.10,
            "rsi_14": 75.0,
            "close_price": 60000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.SELL
        assert signal.confidence == 1.0
        assert "Price at Upper Band" in signal.reason
        assert "RSI Overbought" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_signal_filtered_rsi(self, strategy):
        indicators = {
            "bb_upper_dist": -0.001,
            "bb_lower_dist": 0.10,
            "rsi_14": 65.0,
            "close_price": 60000.0,
        }
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "RSI not overbought" in signal.reason
