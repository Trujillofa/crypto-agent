import pytest

from src.strategy.bollinger_strategy import BollingerBounceStrategy
from src.strategy.macd_strategy import MACDHistogramStrategy
from src.strategy.signals import SignalType


class TestStrategyConfidence:
    @pytest.mark.asyncio
    async def test_macd_confidence_scaling(self):
        strategy = MACDHistogramStrategy({"use_atr_filter": False})

        # Initial state: negative histogram (price > ema_50 = uptrend for BUY)
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -10.0, "ema_50": 49000.0, "close_price": 50000.0}
        )

        # Case 1: Weak crossover (small hist relative to price)
        # hist=10, price=50000. Ratio = 0.0002. Bonus = min(0.5, 0.0002 * 250) = 0.05. Conf = 0.55
        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 10.0, "ema_50": 49000.0, "close_price": 50000.0}
        )
        assert signal.type == SignalType.BUY
        assert abs(signal.confidence - 0.55) < 0.01

        # Reset
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -10.0, "ema_50": 49000.0, "close_price": 50000.0}
        )

        # Case 2: Strong crossover (large hist relative to price)
        # hist=1000, price=50000. Ratio = 0.02. Bonus = min(0.5, 0.02 * 250) = 0.5 (cap). Conf = 1.0
        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 1000.0, "ema_50": 49000.0, "close_price": 50000.0}
        )
        assert signal.type == SignalType.BUY
        assert abs(signal.confidence - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_bollinger_confidence_scaling(self):
        strategy = BollingerBounceStrategy(
            {
                "band_distance_threshold": 0.01,
                "rsi_oversold": 30.0,
                "rsi_overbought": 70.0,
            }
        )

        # Case 1: Deep RSI oversold, in uptrend (price > ema_50)
        # RSI 20 (10 below 30). Boost = min(0.5, 10 * 0.05) = 0.5. Conf = 1.0
        signal = await strategy.evaluate(
            "BTCUSDT",
            {
                "bb_lower_dist": 0.0,
                "bb_upper_dist": 0.1,
                "rsi_14": 20.0,
                "close_price": 50000.0,
                "ema_50": 49000.0,
            },
        )
        assert signal.type == SignalType.BUY
        assert abs(signal.confidence - 1.0) < 0.01

        # Case 2: Shallow RSI oversold, in uptrend
        # RSI 29 (1 below 30). Boost = min(0.5, 1 * 0.05) = 0.05. Conf = 0.55
        signal = await strategy.evaluate(
            "BTCUSDT",
            {
                "bb_lower_dist": 0.0,
                "bb_upper_dist": 0.1,
                "rsi_14": 29.0,
                "close_price": 50000.0,
                "ema_50": 49000.0,
            },
        )
        assert signal.type == SignalType.BUY
        assert abs(signal.confidence - 0.55) < 0.01
