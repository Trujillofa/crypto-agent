import pytest
from src.strategy.vwap_strategy import VWAPReversionStrategy
from src.strategy.signals import SignalType


class TestVWAPReversionStrategy:
    @pytest.fixture
    def strategy(self):
        return VWAPReversionStrategy(
            {"vwap_atr_multiplier": 1.5, "rsi_oversold": 40.0, "rsi_overbought": 60.0}
        )

    # 1. Missing required indicator → ValueError
    @pytest.mark.asyncio
    async def test_missing_vwap_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"close_price": 50000.0, "atr_14": 500.0, "rsi_14": 35.0})

    @pytest.mark.asyncio
    async def test_missing_atr14_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"vwap": 51000.0, "close_price": 50000.0, "rsi_14": 35.0})

    @pytest.mark.asyncio
    async def test_missing_rsi14_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"vwap": 51000.0, "close_price": 50000.0, "atr_14": 500.0})

    @pytest.mark.asyncio
    async def test_missing_close_price_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"vwap": 51000.0, "atr_14": 500.0, "rsi_14": 35.0})

    # 2. None indicator (warmup) → HOLD
    @pytest.mark.asyncio
    async def test_none_vwap_returns_hold(self, strategy):
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": None, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 35.0}
        )
        assert signal.type == SignalType.HOLD
        assert "Waiting" in signal.reason

    @pytest.mark.asyncio
    async def test_none_atr14_returns_hold(self, strategy):
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 51000.0, "close_price": 50000.0, "atr_14": None, "rsi_14": 35.0}
        )
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_none_rsi14_returns_hold(self, strategy):
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 51000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": None}
        )
        assert signal.type == SignalType.HOLD

    # 3. BUY signal fires correctly
    @pytest.mark.asyncio
    async def test_buy_signal_price_below_vwap(self, strategy):
        # VWAP=51000, price=50000, deviation=1000, ATR14=500, threshold=750, RSI=35 < 40
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 51000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 35.0}
        )
        assert signal.type == SignalType.BUY
        assert signal.confidence > 0.5
        assert "below VWAP" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_requires_rsi_below_oversold(self, strategy):
        """BUY needs RSI < 40; RSI=50 should HOLD."""
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 51000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 50.0}
        )
        assert signal.type == SignalType.HOLD
        assert "RSI" in signal.reason

    # 4. SELL signal fires correctly
    @pytest.mark.asyncio
    async def test_sell_signal_price_above_vwap(self, strategy):
        # VWAP=49000, price=50000, deviation=1000, ATR14=500, threshold=750, RSI=65 > 60
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 49000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 65.0}
        )
        assert signal.type == SignalType.SELL
        assert signal.confidence > 0.5
        assert "above VWAP" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_requires_rsi_above_overbought(self, strategy):
        """SELL needs RSI > 60; RSI=55 should HOLD."""
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 49000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 55.0}
        )
        assert signal.type == SignalType.HOLD

    # 5. Small deviation blocks signal (below threshold)
    @pytest.mark.asyncio
    async def test_buy_blocked_by_small_deviation(self, strategy):
        # VWAP=50100, price=50000, deviation=100, ATR14=500, threshold=750 → deviation < threshold
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 50100.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 30.0}
        )
        assert signal.type == SignalType.HOLD
        assert "below threshold" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_blocked_by_small_deviation(self, strategy):
        # VWAP=49900, price=50000, deviation=100, ATR14=500, threshold=750 → deviation < threshold
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 49900.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 70.0}
        )
        assert signal.type == SignalType.HOLD

    # 6. HOLD when conditions partially met
    @pytest.mark.asyncio
    async def test_hold_when_only_deviation_met(self, strategy):
        """Large deviation but RSI neutral — no signal."""
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 52000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 50.0}
        )
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_hold_when_only_rsi_met(self, strategy):
        """RSI oversold but price close to VWAP — no signal."""
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 50050.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 30.0}
        )
        assert signal.type == SignalType.HOLD

    # 7. Confidence scaling
    @pytest.mark.asyncio
    async def test_buy_confidence_scales_with_excess_deviation(self, strategy):
        # threshold = 1.5 * 500 = 750
        # Small excess: deviation=800 → excess_ratio = 800/750 - 1 = 0.067 → confidence ~ 0.527
        s1 = await strategy.evaluate(
            "BTCUSDT", {"vwap": 50800.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 30.0}
        )

        # Large excess: deviation=2000 → excess_ratio = 2000/750 - 1 = 1.67 → bonus capped at 0.4
        s2 = await strategy.evaluate(
            "BTCUSDT", {"vwap": 52000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 30.0}
        )

        assert s1.type == SignalType.BUY
        assert s2.type == SignalType.BUY
        assert s2.confidence > s1.confidence

    @pytest.mark.asyncio
    async def test_confidence_capped_at_0_9(self, strategy):
        """Extremely large deviation should not exceed 0.9 confidence."""
        signal = await strategy.evaluate(
            "BTCUSDT", {"vwap": 60000.0, "close_price": 50000.0, "atr_14": 500.0, "rsi_14": 20.0}
        )
        assert signal.confidence <= 0.9

    # 8. get_name
    def test_get_name(self, strategy):
        assert "VWAP" in strategy.get_name()
        assert "1.5" in strategy.get_name()
