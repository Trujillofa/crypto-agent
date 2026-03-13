import pytest

from src.strategy.signals import SignalType
from src.strategy.trend_pullback import TrendPullbackStrategy


class TestTrendPullbackStrategy:
    @pytest.fixture
    def strategy(self):
        return TrendPullbackStrategy(
            {
                "rsi_reclaim_level": 48.0,
                "min_trend_strength_pct": 0.008,
                "max_pullback_distance_pct": 0.02,
                "vwap_pullback_distance_pct": 0.03,
                "min_atr_pct": 0.008,
                "min_macd_hist": -0.01,
                "strong_trend_strength_pct": 0.015,
                "continuation_rsi_level": 54.0,
                "continuation_max_vwap_distance_pct": 0.04,
                "continuation_max_ema50_extension_pct": 0.03,
                "continuation_min_macd_hist": -0.01,
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
                "rsi_14": 50.0,
                "atr_pct": 0.02,
                "macd_hist": 0.1,
                "vwap": 99.5,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "Waiting for trend pullback indicators" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_on_recovery_in_uptrend(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 100.0,
                "ema_200": 97.5,
                "rsi_14": 47.0,
                "atr_pct": 0.02,
                "macd_hist": -0.01,
                "vwap": 100.0,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.6,
                "ema_50": 100.0,
                "ema_200": 97.5,
                "rsi_14": 51.0,
                "atr_pct": 0.02,
                "macd_hist": 0.05,
                "vwap": 100.2,
            },
        )
        assert signal.type == SignalType.BUY
        assert signal.confidence > 0.55
        assert "Trend pullback recovered" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_vwap_pullback_is_too_extended(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 48.0,
                "atr_pct": 0.02,
                "macd_hist": -0.02,
                "vwap": 100.0,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.7,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 52.0,
                "atr_pct": 0.02,
                "macd_hist": 0.03,
                "vwap": 97.0,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "near_vwap=False" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_on_strong_trend_continuation(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.8,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 53.0,
                "atr_pct": 0.02,
                "macd_hist": -0.02,
                "vwap": 101.0,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.6,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 56.0,
                "atr_pct": 0.02,
                "macd_hist": -0.005,
                "vwap": 101.0,
            },
        )
        assert signal.type == SignalType.BUY
        assert "Trend continuation confirmed" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_on_deep_reclaim_when_enabled(self):
        strategy = TrendPullbackStrategy(
            {
                "rsi_reclaim_level": 48.0,
                "min_trend_strength_pct": 0.008,
                "max_pullback_distance_pct": 0.02,
                "vwap_pullback_distance_pct": 0.02,
                "min_atr_pct": 0.008,
                "min_macd_hist": -0.01,
                "strong_trend_strength_pct": 0.015,
                "continuation_rsi_level": 54.0,
                "continuation_max_vwap_distance_pct": 0.03,
                "continuation_max_ema50_extension_pct": 0.025,
                "continuation_min_macd_hist": -0.01,
                "deep_reclaim_enabled": True,
                "deep_reclaim_rsi_level": 50.0,
                "deep_reclaim_min_macd_hist": 0.0,
                "deep_reclaim_max_ema50_extension_pct": 0.03,
                "deep_reclaim_max_vwap_distance_pct": 0.03,
                "deep_reclaim_arm_window_bars": 3,
                "deep_reclaim_arm_max_ema50_distance_pct": 0.03,
                "deep_reclaim_arm_max_vwap_distance_pct": 0.03,
            }
        )
        first_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 99.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 47.0,
                "atr_pct": 0.02,
                "macd_hist": -0.02,
                "vwap": 100.0,
            },
        )
        assert first_signal.type == SignalType.HOLD
        assert "deep_reclaim_armed_bars=3" in first_signal.reason
        armed_signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 99.7,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 47.5,
                "atr_pct": 0.02,
                "macd_hist": -0.015,
                "vwap": 100.1,
            },
        )
        assert armed_signal.type == SignalType.HOLD
        assert "deep_reclaim_armed_bars=3" in armed_signal.reason
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.6,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 51.0,
                "atr_pct": 0.02,
                "macd_hist": 0.02,
                "vwap": 100.4,
            },
        )
        assert signal.type == SignalType.BUY
        assert "Trend deep reclaim confirmed" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_on_deep_reclaim_setup_when_disabled(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 99.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 47.0,
                "atr_pct": 0.02,
                "macd_hist": -0.02,
                "vwap": 100.0,
            },
        )
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 99.7,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 47.5,
                "atr_pct": 0.02,
                "macd_hist": -0.015,
                "vwap": 100.1,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.6,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 51.0,
                "atr_pct": 0.02,
                "macd_hist": 0.02,
                "vwap": 100.4,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "deep_reclaim_entry=False" in signal.reason

    @pytest.mark.asyncio
    async def test_deep_reclaim_arm_expires_without_reclaim(self):
        strategy = TrendPullbackStrategy(
            {
                "rsi_reclaim_level": 48.0,
                "min_trend_strength_pct": 0.008,
                "max_pullback_distance_pct": 0.02,
                "vwap_pullback_distance_pct": 0.02,
                "min_atr_pct": 0.008,
                "min_macd_hist": -0.01,
                "strong_trend_strength_pct": 0.015,
                "continuation_rsi_level": 54.0,
                "continuation_max_vwap_distance_pct": 0.03,
                "continuation_max_ema50_extension_pct": 0.025,
                "continuation_min_macd_hist": -0.01,
                "deep_reclaim_enabled": True,
                "deep_reclaim_rsi_level": 50.0,
                "deep_reclaim_min_macd_hist": 0.0,
                "deep_reclaim_arm_window_bars": 2,
            }
        )
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 99.4,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 47.0,
                "atr_pct": 0.02,
                "macd_hist": -0.02,
                "vwap": 100.0,
            },
        )
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 99.9,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 47.5,
                "atr_pct": 0.02,
                "macd_hist": -0.015,
                "vwap": 100.2,
            },
        )
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 48.0,
                "atr_pct": 0.02,
                "macd_hist": -0.01,
                "vwap": 100.3,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 102.6,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 51.0,
                "atr_pct": 0.02,
                "macd_hist": 0.02,
                "vwap": 100.4,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "deep_reclaim_armed_bars=0" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_trend_is_weak(self, strategy):
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.3,
                "ema_50": 100.0,
                "ema_200": 99.6,
                "rsi_14": 53.0,
                "atr_pct": 0.02,
                "macd_hist": 0.05,
                "vwap": 100.2,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "trend=False" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_far_from_ema50(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 104.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 49.0,
                "atr_pct": 0.02,
                "macd_hist": 0.05,
                "vwap": 103.5,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 104.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 53.0,
                "atr_pct": 0.02,
                "macd_hist": 0.05,
                "vwap": 103.5,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "near_ema50=False" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_recovery_conditions_are_not_met(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 49.0,
                "atr_pct": 0.02,
                "macd_hist": 0.01,
                "vwap": 100.1,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.1,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 48.0,
                "atr_pct": 0.02,
                "macd_hist": 0.0,
                "vwap": 100.1,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "recovery_ok=False" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_atr_filter_blocks_chop(self, strategy):
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 100.2,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 52.0,
                "atr_pct": 0.005,
                "macd_hist": 0.05,
                "vwap": 100.0,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "trend=False" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_continuation_is_too_extended_from_ema50(self, strategy):
        await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 101.0,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 54.0,
                "atr_pct": 0.02,
                "macd_hist": -0.01,
                "vwap": 101.0,
            },
        )
        signal = await strategy.evaluate(
            "SOLUSDT",
            {
                "close_price": 104.5,
                "ema_50": 100.0,
                "ema_200": 97.0,
                "rsi_14": 58.0,
                "atr_pct": 0.02,
                "macd_hist": 0.02,
                "vwap": 103.0,
            },
        )
        assert signal.type == SignalType.HOLD
        assert "continuation_entry=False" in signal.reason

    def test_get_name(self, strategy):
        assert strategy.get_name() == "TrendPullback"
