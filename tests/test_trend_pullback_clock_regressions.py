"""Out-of-order and revised closed-bar snapshots must not corrupt recovery state."""

from datetime import UTC, datetime

import pytest

from src.strategy.signals import SignalType
from src.strategy.trend_pullback import TrendPullbackStrategy
from tests.test_trend_pullback_bar_state import BARS, SYMBOL


@pytest.mark.asyncio
async def test_corrected_same_bar_is_the_next_bar_reference() -> None:
    corrected = dict(BARS[1], rsi_14=56.0, macd_hist=0.07, close_price=100.8)
    next_bar = dict(BARS[2], rsi_14=53.0, macd_hist=0.06)
    polled = TrendPullbackStrategy()
    replay = TrendPullbackStrategy()
    for bar in (BARS[0], BARS[1], corrected):
        await polled.evaluate(SYMBOL, bar)
    for bar in (BARS[0], corrected):
        await replay.evaluate(SYMBOL, bar)
    actual = await polled.evaluate(SYMBOL, next_bar)
    expected = await replay.evaluate(SYMBOL, next_bar)
    assert actual.type == expected.type == SignalType.HOLD
    assert polled._previous_bar[SYMBOL].rsi == 56.0
    assert polled._previous_bar[SYMBOL].macd_hist == 0.07
    assert polled._previous_bar[SYMBOL].close == 100.8


@pytest.mark.asyncio
async def test_older_bar_is_hold_and_cannot_rewind_any_state() -> None:
    polled = TrendPullbackStrategy({"deep_reclaim_enabled": True})
    ordered = TrendPullbackStrategy({"deep_reclaim_enabled": True})
    for bar in BARS[:3]:
        await polled.evaluate(SYMBOL, bar)
        await ordered.evaluate(SYMBOL, bar)
    before = (
        polled._seen_bar.copy(),
        polled._previous_bar.copy(),
        polled._deep_reclaim_armed_bars_remaining.copy(),
    )
    assert (await polled.evaluate(SYMBOL, BARS[0])).type == SignalType.HOLD
    assert before == (
        polled._seen_bar,
        polled._previous_bar,
        polled._deep_reclaim_armed_bars_remaining,
    )
    target = dict(BARS[3], rsi_14=50.0)
    assert (
        (await polled.evaluate(SYMBOL, target)).type
        == (await ordered.evaluate(SYMBOL, target)).type
        == SignalType.HOLD
    )


@pytest.mark.asyncio
async def test_equivalent_clocks_and_bad_clocks_fail_closed_per_symbol() -> None:
    strategy = TrendPullbackStrategy()
    await strategy.evaluate(SYMBOL, BARS[0])
    await strategy.evaluate(SYMBOL, BARS[1])
    corrected = dict(
        BARS[1],
        time=datetime(2026, 11, 16, 5, tzinfo=UTC).isoformat().replace("+00:00", "+01:00"),
        rsi_14=56.0,
    )
    await strategy.evaluate(SYMBOL, corrected)
    assert strategy._previous_bar[SYMBOL].bar_time == BARS[0]["time"]
    seen = strategy._seen_bar.copy()
    for clock in ("not-a-time", 123, None, "2026-11-16T02:00:00Z"):
        assert (await strategy.evaluate(SYMBOL, dict(BARS[3], time=clock))).type == SignalType.HOLD
        assert strategy._seen_bar == seen
    assert (await strategy.evaluate("OTHER", BARS[0])).type == SignalType.HOLD
    assert (await strategy.evaluate("OTHER", BARS[1])).type == SignalType.BUY
