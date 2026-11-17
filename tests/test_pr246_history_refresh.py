from __future__ import annotations

import pytest

from src.features.reader import IndicatorReader
from src.strategy.engine import EngineConfig, StrategyEngine
from src.strategy.signals import SignalType
from src.strategy.trend_pullback import TrendPullbackStrategy
from tests.test_trend_pullback_bar_state import BARS, SYMBOL


def rows(*bars):
    return [dict(bar, ema_12=101.0, ema_26=100.0) for bar in bars]


@pytest.mark.asyncio
async def test_engine_refreshes_corrected_previous_bar_without_replaying_it():
    reader = IndicatorReader({})
    data = rows(*BARS[:2])

    async def fetch(*args):
        return data

    reader._fetch_rows = fetch
    engine = StrategyEngine(
        EngineConfig(
            symbols=[SYMBOL],
            timeframe="4h",
            cooldown_candles=0,
            strategy_classes=[TrendPullbackStrategy],
        ),
        reader,
    )
    fired = []

    async def callback(signal):
        fired.append(signal.type)

    await engine._evaluate_all(callback)
    assert fired == [SignalType.BUY]
    data[0]["rsi_14"] = 56.0
    await engine._evaluate_all(callback)
    assert fired == [SignalType.BUY]
    strategy = engine._strategies[SYMBOL][0]
    assert strategy._previous_bar[SYMBOL].rsi == 56.0
    data[0]["rsi_14"] = 47.0
    await engine._evaluate_all(callback)
    assert fired == [SignalType.BUY, SignalType.BUY]  # retry remains possible


@pytest.mark.asyncio
async def test_next_bar_uses_corrected_seen_row_and_ignores_unmatched_or_invalid_rows():
    s = TrendPullbackStrategy()
    await s.evaluate(SYMBOL, BARS[0])
    corrected = dict(BARS[0], rsi_14=56.0)
    s.refresh_previous_bar(SYMBOL, corrected, BARS[1])
    assert (await s.evaluate(SYMBOL, BARS[1])).type == SignalType.HOLD
    s.refresh_previous_bar(SYMBOL, dict(BARS[0], rsi_14=float("nan")), BARS[1])
    assert s._previous_bar[SYMBOL].rsi == 56.0
    s.refresh_previous_bar(SYMBOL, BARS[2], BARS[1])
    assert s._previous_bar[SYMBOL].rsi == 56.0
    s.refresh_previous_bar(SYMBOL, BARS[0], BARS[0])
    assert s._previous_bar[SYMBOL].rsi == 56.0


@pytest.mark.asyncio
async def test_skipped_history_not_silently_installed_as_seen_reference():
    s = TrendPullbackStrategy()
    await s.evaluate(SYMBOL, BARS[0])
    s.refresh_previous_bar(SYMBOL, BARS[2], BARS[3])
    assert s._seen_bar[SYMBOL].bar_time == BARS[0]["time"]
    await s.evaluate(SYMBOL, BARS[3])
    assert s._previous_bar[SYMBOL].bar_time == BARS[0]["time"]
