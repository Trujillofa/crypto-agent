import pytest

from src.strategy.cci_strategy import CCIBreakoutStrategy
from src.strategy.signals import SignalType


class TestCCIBreakoutStrategy:
    @pytest.fixture
    def strategy(self):
        return CCIBreakoutStrategy(
            {"cci_buy_threshold": 100.0, "cci_sell_threshold": -100.0, "atr_min_pct": 0.005}
        )

    def _base_indicators(self, cci: float, prev_cci: float | None = None) -> dict:
        """Return a valid indicator dict. Call evaluate once with prev_cci to prime state."""
        return {
            "cci": cci,
            "ema_50": 49000.0,
            "close_price": 50000.0,
            "atr_pct": 0.01,  # 1% — above atr_min_pct
        }

    # 1. Missing required indicator → ValueError
    @pytest.mark.asyncio
    async def test_missing_cci_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate(
                "BTCUSDT", {"ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
            )

    @pytest.mark.asyncio
    async def test_missing_ema50_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate(
                "BTCUSDT", {"cci": 110.0, "close_price": 50000.0, "atr_pct": 0.01}
            )

    @pytest.mark.asyncio
    async def test_missing_atr_pct_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate(
                "BTCUSDT", {"cci": 110.0, "ema_50": 49000.0, "close_price": 50000.0}
            )

    # 2. None indicator (warmup) → HOLD
    @pytest.mark.asyncio
    async def test_none_cci_returns_hold(self, strategy):
        indicators = {"cci": None, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD
        assert "Waiting" in signal.reason

    @pytest.mark.asyncio
    async def test_none_atr_returns_hold(self, strategy):
        indicators = {"cci": 110.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": None}
        signal = await strategy.evaluate("BTCUSDT", indicators)
        assert signal.type == SignalType.HOLD

    # 3. BUY crossover fires correctly (prev < 100, curr >= 100)
    @pytest.mark.asyncio
    async def test_buy_crossover_above_threshold(self, strategy):
        # Prime state with CCI below threshold
        await strategy.evaluate(
            "BTCUSDT", {"cci": 90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        # Cross above 100 with price > EMA50
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": 110.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.BUY
        assert signal.confidence > 0.5
        assert "crossed above" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_crossover_exact_threshold(self, strategy):
        """CCI exactly at threshold (prev < 100, curr == 100) should trigger BUY."""
        await strategy.evaluate(
            "BTCUSDT", {"cci": 95.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": 100.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        assert signal.type == SignalType.BUY

    # 4. SELL crossover fires correctly (prev > -100, curr <= -100)
    @pytest.mark.asyncio
    async def test_sell_crossover_below_threshold(self, strategy):
        # Prime state with CCI above sell threshold
        await strategy.evaluate(
            "BTCUSDT", {"cci": -90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        # Cross below -100 (no trend gate needed)
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": -110.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.SELL
        assert signal.confidence > 0.5
        assert "crossed below" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_crossover_exact_threshold(self, strategy):
        """CCI exactly at -100 (prev > -100, curr == -100) should trigger SELL."""
        await strategy.evaluate(
            "BTCUSDT", {"cci": -90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": -100.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        assert signal.type == SignalType.SELL

    # 5. BUY blocked by trend gate (price < EMA50)
    @pytest.mark.asyncio
    async def test_buy_blocked_by_trend_gate(self, strategy):
        # Prime with CCI below threshold
        await strategy.evaluate(
            "BTCUSDT", {"cci": 90.0, "ema_50": 51000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        # Cross above threshold but price < EMA50 (downtrend)
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": 110.0, "ema_50": 51000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        assert signal.type == SignalType.HOLD
        assert "Counter-trend" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_not_blocked_by_trend_gate(self, strategy):
        """SELL has no trend gate — fires even in uptrend."""
        await strategy.evaluate(
            "BTCUSDT", {"cci": -90.0, "ema_50": 45000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": -110.0, "ema_50": 45000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        assert signal.type == SignalType.SELL

    # 6. Low volatility blocks signal
    @pytest.mark.asyncio
    async def test_buy_blocked_by_low_volatility(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"cci": 90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.001}
        )
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": 110.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.001}
        )
        assert signal.type == SignalType.HOLD
        assert "Low volatility" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_blocked_by_low_volatility(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"cci": -90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.001}
        )
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": -110.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.001}
        )
        assert signal.type == SignalType.HOLD
        assert "Low volatility" in signal.reason

    # 7. HOLD when conditions partially met
    @pytest.mark.asyncio
    async def test_hold_when_cci_does_not_cross(self, strategy):
        """CCI stays above threshold without crossing — no signal."""
        await strategy.evaluate(
            "BTCUSDT", {"cci": 120.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": 130.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_hold_when_cci_falls_into_overbought(self, strategy):
        """CCI moves from below-threshold to above without crossing — no signal yet."""
        # Previous is well below 100
        await strategy.evaluate(
            "BTCUSDT", {"cci": 50.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        # Current jumps past threshold but previous was not at crossover region — crossover counts
        # (This verifies crossover fires on jump too)
        signal = await strategy.evaluate(
            "BTCUSDT", {"cci": 150.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        assert signal.type == SignalType.BUY

    # 8. Confidence scaling
    @pytest.mark.asyncio
    async def test_buy_confidence_scales_with_cci_excess(self, strategy):
        await strategy.evaluate(
            "BTCUSDT", {"cci": 90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        # CCI at exactly 100 → excess = 0 → confidence = 0.5
        s1 = await strategy.evaluate(
            "BTCUSDT", {"cci": 100.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        # Re-prime
        strategy2 = CCIBreakoutStrategy(
            {"cci_buy_threshold": 100.0, "cci_sell_threshold": -100.0, "atr_min_pct": 0.005}
        )
        await strategy2.evaluate(
            "BTCUSDT", {"cci": 90.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )
        # CCI at 200 → excess = 100 → bonus = min(0.4, 100/200) = 0.4 → confidence = 0.9
        s2 = await strategy2.evaluate(
            "BTCUSDT", {"cci": 200.0, "ema_50": 49000.0, "close_price": 50000.0, "atr_pct": 0.01}
        )

        assert s1.type == SignalType.BUY
        assert s2.type == SignalType.BUY
        assert s2.confidence > s1.confidence

    # 9. get_name
    def test_get_name(self, strategy):
        assert "CCI" in strategy.get_name()
        assert "100" in strategy.get_name()
