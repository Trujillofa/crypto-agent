"""Tests for the long-only daily trend strategy."""

from __future__ import annotations

import pytest

from src.strategy.daily_trend_long import DailyTrendLong
from src.strategy.signals import SignalType


def _indicators(*, close: float, sma: float | None) -> dict[str, float | None]:
    return {"close_price": close, "sma_50": sma}


class TestDailyTrendLong:
    @pytest.fixture
    def strategy(self) -> DailyTrendLong:
        return DailyTrendLong({"sma_window": 50})

    @pytest.mark.asyncio
    async def test_missing_indicator_raises(self, strategy: DailyTrendLong) -> None:
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"close_price": 100.0})

    @pytest.mark.asyncio
    async def test_insufficient_history_returns_hold(self, strategy: DailyTrendLong) -> None:
        signal = await strategy.evaluate("BTCUSDT", _indicators(close=100.0, sma=None))
        assert signal.type == SignalType.HOLD
        assert "Waiting for SMA(50)" in signal.reason

    @pytest.mark.asyncio
    async def test_close_above_sma_emits_buy(self, strategy: DailyTrendLong) -> None:
        signal = await strategy.evaluate("BTCUSDT", _indicators(close=105.0, sma=100.0))
        assert signal.type == SignalType.BUY
        assert "above SMA(50)" in signal.reason

    @pytest.mark.asyncio
    async def test_close_below_sma_emits_sell_to_flat(self, strategy: DailyTrendLong) -> None:
        signal = await strategy.evaluate("BTCUSDT", _indicators(close=95.0, sma=100.0))
        assert signal.type == SignalType.SELL
        assert "exit to flat" in signal.reason

    @pytest.mark.asyncio
    async def test_long_only_never_targets_short(self, strategy: DailyTrendLong) -> None:
        """SELL means flatten only; BUY is the sole long-entry signal."""
        buy = await strategy.evaluate("BTCUSDT", _indicators(close=110.0, sma=100.0))
        sell = await strategy.evaluate("BTCUSDT", _indicators(close=90.0, sma=100.0))
        assert buy.type == SignalType.BUY
        assert sell.type == SignalType.SELL
        assert "short" not in sell.reason.lower()

    @pytest.mark.asyncio
    async def test_switch_behaviour_on_synthetic_series(self) -> None:
        """Crossing SMA toggles BUY/SELL like the Gate 1 probe semantics."""
        strategy = DailyTrendLong({"sma_window": 5})
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 103.0, 101.0, 99.0]
        sma_values: list[float | None] = [None, None, None, None, 102.0, 103.0, 103.4, 102.6, 101.6]

        signals: list[SignalType] = []
        for close, sma in zip(prices, sma_values, strict=True):
            signal = await strategy.evaluate(
                "BTCUSDT",
                {"close_price": close, "sma_5": sma},
            )
            if signal.type != SignalType.HOLD:
                signals.append(signal.type)

        assert SignalType.HOLD in [
            (
                await strategy.evaluate(
                    "BTCUSDT",
                    {"close_price": 100.0, "sma_5": None},
                )
            ).type
        ]
        assert signals.count(SignalType.BUY) >= 1
        assert signals.count(SignalType.SELL) >= 1
        assert SignalType.BUY in signals
        assert signals[-1] == SignalType.SELL

    @pytest.mark.asyncio
    async def test_respects_configured_sma_window(self) -> None:
        strategy = DailyTrendLong({"sma_window": 40})
        with pytest.raises(ValueError, match="sma_40"):
            await strategy.evaluate("ETHUSDT", {"close_price": 100.0, "sma_50": 99.0})

        signal = await strategy.evaluate(
            "ETHUSDT",
            {"close_price": 100.0, "sma_40": 99.0},
        )
        assert signal.type == SignalType.BUY
