import pytest

from src.strategy.breakout_retest import BreakoutRetestStrategy
from src.strategy.signals import SignalType


class TestBreakoutRetestStrategy:
    @pytest.fixture
    def strategy(self):
        return BreakoutRetestStrategy(
            {
                "min_trend_strength_pct": 0.008,
                "min_atr_pct": 0.008,
                "breakout_rsi_level": 58.0,
                "breakout_min_macd_hist": 0.0,
                "breakout_band_distance_threshold": 0.01,
                "breakout_min_vwap_extension_pct": 0.01,
                "retest_window_bars": 4,
                "retest_vwap_distance_pct": 0.015,
                "retest_ema50_distance_pct": 0.015,
                "reclaim_rsi_level": 50.0,
                "retest_min_macd_hist": -0.01,
                "max_extension_after_retest_pct": 0.025,
                "min_reclaim_above_levels_pct": 0.002,
            }
        )

    @pytest.mark.asyncio
    async def test_missing_indicator_raises(self, strategy):
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("SOLUSDT", {"close_price": 100.0})

    @pytest.mark.asyncio
    async def test_none_indicator_returns_hold(self, strategy):
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": None,
                "ema_200": 95.0,
                "vwap": 99.0,
                "bb_upper_dist": 0.01,
                "rsi_14": 60.0,
                "macd_hist": 0.05,
                "atr_pct": 0.02,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "Waiting for breakout/retest indicators" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_after_breakout_then_retest(self, strategy):
        """BUY after breakout arms, then retest and reclaim."""
        # Baseline bar (no breakout yet)
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 99.5,
                "ema_200": 97.0,
                "vwap": 99.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 54.0,
                "macd_hist": -0.01,
                "atr_pct": 0.02,
            },
        )
        # Breakout bar (arms the strategy, 4 bars remaining)
        breakout_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.5,
                "bb_upper_dist": 0.005,
                "rsi_14": 60.0,
                "macd_hist": 0.05,
                "atr_pct": 0.02,
            },
        )
        assert breakout_signal.type == SignalType.HOLD
        # Retest with RSI at reclaim level (52.0 >= 50.0) and MACD improving
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.6,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        entry_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert entry_signal.type == SignalType.BUY
        assert "Breakout retest confirmed" in entry_signal.reason

    @pytest.mark.asyncio
    async def test_hold_without_prior_breakout_arm(self, strategy):
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "armed=0" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_retest_window_expires(self, strategy):
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 99.5,
                "ema_200": 97.0,
                "vwap": 99.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 54.0,
                "macd_hist": -0.01,
                "atr_pct": 0.02,
            },
        )
        for offset in range(4):
            await strategy.evaluate(
                "SOLUSDT",
                {
                    "close_price": 101.5 - (offset * 0.1),
                    "ema_50": 100.0,
                    "ema_200": 97.0,
                    "vwap": 101.2,
                    "bb_upper_dist": 0.02,
                    "rsi_14": 55.0,
                    "macd_hist": 0.06,
                    "atr_pct": 0.02,
                },
            )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "armed=0" in signal.reason

    @pytest.mark.asyncio
    async def test_one_entry_per_breakout_arm(self, strategy):
        """After a breakout arms the strategy, only one entry should be allowed."""
        # Baseline bar (no breakout yet)
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 99.5,
                "ema_200": 97.0,
                "vwap": 99.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 54.0,
                "macd_hist": -0.01,
                "atr_pct": 0.02,
            },
        )
        # Breakout bar (arms the strategy, 4 bars remaining)
        breakout_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.5,
                "bb_upper_dist": 0.005,
                "rsi_14": 60.0,
                "macd_hist": 0.05,
                "atr_pct": 0.02,
            },
        )
        assert breakout_signal.type == SignalType.HOLD
        assert "armed=4" in breakout_signal.reason
        # First retest (should trigger entry)
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.6,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        entry_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert entry_signal.type == SignalType.BUY
        assert "Breakout retest confirmed" in entry_signal.reason
        # Second retest within same arm window (should NOT trigger entry - already entered)
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.9,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.8,
                "bb_upper_dist": 0.03,
                "rsi_14": 53.0,
                "macd_hist": 0.06,
                "atr_pct": 0.02,
            },
        )
        hold_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 101.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.9,
                "bb_upper_dist": 0.03,
                "rsi_14": 54.0,
                "macd_hist": 0.07,
                "atr_pct": 0.02,
            },
        )
        assert hold_signal.type == SignalType.HOLD
        # Hold because entry already happened in this arm
        assert (
            "entered_this_arm" in hold_signal.reason
            or "No breakout retest setup" in hold_signal.reason
        )

    @pytest.mark.asyncio
    async def test_strict_reclaim_momentum(self, strategy):
        """Both RSI and MACD must show improvement (was: OR)."""
        # Baseline bar
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 99.5,
                "ema_200": 97.0,
                "vwap": 99.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 54.0,
                "macd_hist": -0.01,
                "atr_pct": 0.02,
            },
        )
        # Breakout bar (arms the strategy, 4 bars remaining)
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.5,
                "bb_upper_dist": 0.005,
                "rsi_14": 60.0,
                "macd_hist": 0.05,
                "atr_pct": 0.02,
            },
        )
        # Retest with RSI improving but MACD flat - should HOLD (new AND logic)
        hold_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.6,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": -0.01,
                "atr_pct": 0.02,
            },
        )
        assert hold_signal.type == SignalType.HOLD
        assert "No breakout retest setup" in hold_signal.reason
        # Retest with both RSI and MACD improving - should BUY
        entry_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert entry_signal.type == SignalType.BUY
        assert "Breakout retest confirmed" in entry_signal.reason

    @pytest.mark.asyncio
    async def test_minimum_reclaim_margin(self, strategy):
        """Price must reclaim above VWAP and EMA50 by minimum margin."""
        # Baseline bar
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 99.5,
                "ema_200": 97.0,
                "vwap": 99.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 54.0,
                "macd_hist": -0.01,
                "atr_pct": 0.02,
            },
        )
        # Breakout bar (arms the strategy, 4 bars remaining)
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.5,
                "bb_upper_dist": 0.005,
                "rsi_14": 60.0,
                "macd_hist": 0.05,
                "atr_pct": 0.02,
            },
        )
        # Retest with price exactly at VWAP (no margin above) - should HOLD
        hold_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.4,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert hold_signal.type == SignalType.HOLD
        assert "No breakout retest setup" in hold_signal.reason
        # Retest with price 0.3% above both VWAP and EMA50 - should BUY
        entry_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "vwap": 100.7,
                "bb_upper_dist": 0.03,
                "rsi_14": 52.0,
                "macd_hist": 0.01,
                "atr_pct": 0.02,
            },
        )
        assert entry_signal.type == SignalType.BUY
        assert "Breakout retest confirmed" in entry_signal.reason

    def test_get_name(self, strategy):
        assert strategy.get_name() == "BreakoutRetest"
