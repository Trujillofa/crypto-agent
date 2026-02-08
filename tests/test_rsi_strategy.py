import pytest
from src.strategy.rsi_reversal import RSIReversalStrategy
from src.strategy.signals import SignalType


class TestRSIReversalStrategy:
    @pytest.fixture
    def strategy(self):
        return RSIReversalStrategy(
            {"oversold_threshold": 30.0, "overbought_threshold": 70.0, "rsi_period": 14}
        )

    @pytest.mark.asyncio
    async def test_missing_indicator(self, strategy):
        indicators = {"close_price": 50000.0}
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", indicators)

    @pytest.mark.asyncio
    async def test_neutral_hold(self, strategy):
        await strategy.evaluate("BTCUSDT", {"rsi_14": 50.0, "close_price": 50000.0})

        signal = await strategy.evaluate(
            "BTCUSDT", {"rsi_14": 55.0, "close_price": 50100.0}
        )

        assert signal.type == SignalType.HOLD
        assert "prev: 50.00" in signal.reason

    @pytest.mark.asyncio
    async def test_bullish_reversal_buy(self, strategy):
        # 1. Previous was oversold (20 < 30)
        await strategy.evaluate("BTCUSDT", {"rsi_14": 20.0, "close_price": 49000.0})

        # 2. Current crosses back up (35 >= 30)
        signal = await strategy.evaluate(
            "BTCUSDT", {"rsi_14": 35.0, "close_price": 49500.0}
        )

        assert signal.type == SignalType.BUY
        assert signal.confidence > 0.5
        assert "crossed above 30.0" in signal.reason

        # Check confidence scaling: depth was 30-20=10. 10/30 = 0.33. 0.5 + 0.33*0.5 = 0.66
        assert 0.6 < signal.confidence < 0.7

    @pytest.mark.asyncio
    async def test_bearish_reversal_sell(self, strategy):
        # 1. Previous was overbought (80 > 70)
        await strategy.evaluate("BTCUSDT", {"rsi_14": 80.0, "close_price": 55000.0})

        # 2. Current crosses back down (65 <= 70)
        signal = await strategy.evaluate(
            "BTCUSDT", {"rsi_14": 65.0, "close_price": 54000.0}
        )

        assert signal.type == SignalType.SELL
        assert signal.confidence > 0.5
        assert "crossed below 70.0" in signal.reason

    @pytest.mark.asyncio
    async def test_entering_oversold_zone_no_signal(self, strategy):
        # Previous 35 (neutral)
        await strategy.evaluate("BTCUSDT", {"rsi_14": 35.0, "close_price": 50000.0})

        # Current 25 (oversold, but no crossover UP)
        signal = await strategy.evaluate(
            "BTCUSDT", {"rsi_14": 25.0, "close_price": 49000.0}
        )

        assert signal.type == SignalType.HOLD
        # We don't buy when it falls INTO oversold, only when it recovers

    @pytest.mark.asyncio
    async def test_entering_overbought_zone_no_signal(self, strategy):
        # Previous 65 (neutral)
        await strategy.evaluate("BTCUSDT", {"rsi_14": 65.0, "close_price": 50000.0})

        # Current 75 (overbought, but no crossover DOWN)
        signal = await strategy.evaluate(
            "BTCUSDT", {"rsi_14": 75.0, "close_price": 51000.0}
        )

        assert signal.type == SignalType.HOLD
