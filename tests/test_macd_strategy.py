import pytest
from src.strategy.macd_strategy import MACDHistogramStrategy
from src.strategy.signals import SignalType


class TestMACDHistogramStrategy:
    @pytest.fixture
    def strategy(self):
        return MACDHistogramStrategy(
            {
                "min_histogram_threshold": 0.0,
                "use_atr_filter": True,
                "atr_min_pct": 0.005,
            }
        )

    @pytest.mark.asyncio
    async def test_warmup_none_value(self, strategy):
        indicators = {"macd_hist": None, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "Waiting for MACD" in signal.reason

    @pytest.mark.asyncio
    async def test_missing_indicator(self, strategy):
        indicators = {"close_price": 50000.0, "ema_50": 49000.0, "atr_pct": 0.01}  # Missing macd_hist
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", indicators)

    @pytest.mark.asyncio
    async def test_neutral_hold(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 10.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 20.0, "ema_50": 49000.0, "close_price": 50100.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.HOLD
        assert "prev: 10.00" in signal.reason

    @pytest.mark.asyncio
    async def test_bullish_crossover(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -10.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 10.0, "ema_50": 49000.0, "close_price": 50100.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.BUY
        assert signal.confidence > 0.5
        assert "Bullish MACD Crossover" in signal.reason

    @pytest.mark.asyncio
    async def test_bearish_crossover(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 10.0, "ema_50": 51000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -10.0, "ema_50": 51000.0, "close_price": 49900.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.SELL
        assert signal.confidence > 0.5
        assert "Bearish MACD Crossover" in signal.reason

    @pytest.mark.asyncio
    async def test_bearish_crossover_fires_above_ema50(self, strategy):
        """SELL should fire even when price > EMA50 (uptrend top scenario)."""
        # price ($68,189) > ema_50 ($67,500) — old gate would have blocked this
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 1.44, "ema_50": 67500.0, "close_price": 68189.0, "atr_pct": 0.01}
        )

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -0.93, "ema_50": 67500.0, "close_price": 68189.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.SELL
        assert signal.confidence > 0.5
        assert "Bearish MACD Crossover" in signal.reason
        assert "EMA50" not in signal.reason  # no longer mentions EMA50 gate

    @pytest.mark.asyncio
    async def test_bullish_crossover_blocked_below_ema50(self, strategy):
        """BUY should still be blocked when price < EMA50 (downtrend)."""
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -10.0, "ema_50": 51000.0, "close_price": 49900.0, "atr_pct": 0.01}
        )

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 10.0, "ema_50": 51000.0, "close_price": 49900.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.HOLD
        assert "Counter-trend" in signal.reason

    @pytest.mark.asyncio
    async def test_atr_filter_blocks_signal(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"macd_hist": -10.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.001}
        )

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 10.0, "ema_50": 49000.0, "close_price": 50100.0, "atr_pct": 0.001}
        )

        assert signal.type == SignalType.HOLD
        assert "Low Volatility" in signal.reason

    @pytest.mark.asyncio
    async def test_min_threshold_logic(self):
        strategy = MACDHistogramStrategy(
            {"min_histogram_threshold": 5.0, "use_atr_filter": False}
        )

        await strategy.evaluate("BTCUSDT", {"macd_hist": -10.0, "ema_50": 49000.0, "close_price": 50000.0})

        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 2.0, "ema_50": 49000.0, "close_price": 50000.0}
        )

        assert signal.type == SignalType.HOLD

        await strategy.evaluate("BTCUSDT", {"macd_hist": -1.0, "ema_50": 49000.0, "close_price": 50000.0})
        signal = await strategy.evaluate(
            "BTCUSDT", {"macd_hist": 6.0, "ema_50": 49000.0, "close_price": 50000.0}
        )

        assert signal.type == SignalType.BUY
