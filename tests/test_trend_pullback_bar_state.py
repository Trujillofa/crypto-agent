"""Per-bar state regression tests for TrendPullbackStrategy (issue #243, CR-5).

The live engine re-evaluates the newest closed indicator bar on every poll (60s),
while the backtest engine evaluates each bar exactly once.  These tests pin the
recovery state to the bar clock so both paths agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.signals import Signal, SignalType
from src.strategy.trend_pullback import TrendPullbackStrategy

SYMBOL = "SOLUSDT"

# Bar 0: pullback bar. Bars 1 and 3 are recovery bars (rising RSI/MACD/price);
# bar 2 fails the RSI-reclaim check because RSI falls against bar 1.
BARS = [
    {
        "time": "2026-11-16T00:00:00+00:00",
        "close_price": 100.0,
        "high_price": 100.2,
        "low_price": 99.8,
        "ema_50": 100.0,
        "ema_200": 97.5,
        "rsi_14": 47.0,
        "atr_pct": 0.02,
        "macd_hist": -0.01,
        "vwap": 100.0,
    },
    {
        "time": "2026-11-16T04:00:00+00:00",
        "close_price": 100.6,
        "high_price": 100.8,
        "low_price": 100.1,
        "ema_50": 100.0,
        "ema_200": 97.5,
        "rsi_14": 51.0,
        "atr_pct": 0.02,
        "macd_hist": 0.05,
        "vwap": 100.2,
    },
    {
        "time": "2026-11-16T08:00:00+00:00",
        "close_price": 100.7,
        "high_price": 100.9,
        "low_price": 100.4,
        "ema_50": 100.1,
        "ema_200": 97.6,
        "rsi_14": 50.0,
        "atr_pct": 0.02,
        "macd_hist": 0.04,
        "vwap": 100.3,
    },
    {
        "time": "2026-11-16T12:00:00+00:00",
        "close_price": 101.0,
        "high_price": 101.2,
        "low_price": 100.6,
        "ema_50": 100.2,
        "ema_200": 97.7,
        "rsi_14": 55.0,
        "atr_pct": 0.02,
        "macd_hist": 0.06,
        "vwap": 100.4,
    },
]

EXPECTED = [SignalType.HOLD, SignalType.BUY, SignalType.HOLD, SignalType.BUY]


class _CapturingTrendPullback(TrendPullbackStrategy):
    """Records the strategy-level verdicts the backtest engine actually sees."""

    captured: ClassVar[list[SignalType]] = []

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        signal = await super().evaluate(symbol, indicators)
        self.captured.append(signal.type)
        return signal


def _build_reader(data: list[dict[str, object]]) -> IndicatorReader:
    reader = IndicatorReader({})
    reader._connected = True

    async def _mock_fetch(*_args: object) -> list[dict[str, object]]:
        return data

    reader.fetch_range = _mock_fetch
    return reader


@pytest.mark.asyncio
async def test_repeated_polls_within_one_bar_equal_a_single_evaluation() -> None:
    """N polls of the same closed bar must not change the verdict (live vs backtest parity)."""
    polled = TrendPullbackStrategy()
    await polled.evaluate(SYMBOL, BARS[0])

    single = TrendPullbackStrategy()
    await single.evaluate(SYMBOL, BARS[0])
    once = await single.evaluate(SYMBOL, BARS[1])

    polls = [await polled.evaluate(SYMBOL, BARS[1]) for _ in range(4)]

    assert once.type == SignalType.BUY
    assert [(sig.type, sig.confidence, sig.reason) for sig in polls] == [
        (once.type, once.confidence, once.reason)
    ] * len(polls)


@pytest.mark.asyncio
async def test_recovery_state_advances_on_the_bar_clock_not_the_poll_count() -> None:
    """The reference bar must stay fixed inside a bar and move when a new bar closes."""
    strategy = TrendPullbackStrategy()

    await strategy.evaluate(SYMBOL, BARS[0])
    assert strategy._previous_bar.get(SYMBOL) is None

    for _ in range(3):
        assert (await strategy.evaluate(SYMBOL, BARS[1])).type == SignalType.BUY
    assert strategy._previous_bar[SYMBOL].bar_time == BARS[0]["time"]

    # Bar 2's RSI falls against bar 1, so the reclaim check must still be evaluated
    # against bar 1 even though bar 2 has now been polled three times.
    for _ in range(3):
        assert (await strategy.evaluate(SYMBOL, BARS[2])).type == SignalType.HOLD
    assert strategy._previous_bar[SYMBOL].bar_time == BARS[1]["time"]

    assert (await strategy.evaluate(SYMBOL, BARS[3])).type == SignalType.BUY
    assert strategy._previous_bar[SYMBOL].bar_time == BARS[2]["time"]


@pytest.mark.asyncio
async def test_clockless_callers_keep_per_call_behaviour() -> None:
    """Callers that pass no bar clock still compare against the previous evaluation."""
    strategy = TrendPullbackStrategy()
    clockless = [{key: value for key, value in bar.items() if key != "time"} for bar in BARS]

    await strategy.evaluate(SYMBOL, clockless[0])
    assert (await strategy.evaluate(SYMBOL, clockless[1])).type == SignalType.BUY
    # Second evaluation of the same values has no new bar to compare against.
    assert (await strategy.evaluate(SYMBOL, clockless[1])).type == SignalType.HOLD


@pytest.mark.asyncio
async def test_backtest_replay_matches_one_evaluation_per_closed_bar() -> None:
    """Replaying closed bars through evaluate() reproduces the backtest engine's signals."""
    manual: list[SignalType] = []
    strategy = TrendPullbackStrategy()
    for bar in BARS:
        manual.append((await strategy.evaluate(SYMBOL, bar)).type)

    config = BacktestConfig(
        symbol=SYMBOL,
        timeframe="4h",
        start_date="2026-11-16",
        end_date="2026-11-17",
        strategy_classes=[_CapturingTrendPullback],
    )
    _CapturingTrendPullback.captured = []
    engine = BacktestEngine(config, _build_reader(BARS))
    await engine.run()

    assert manual == EXPECTED
    assert _CapturingTrendPullback.captured == manual


@pytest.mark.asyncio
async def test_live_polling_pattern_reproduces_the_single_evaluation_verdicts() -> None:
    """The engine's 60s polling of one closed bar must not change the per-bar verdicts."""
    strategy = TrendPullbackStrategy()
    # The engine primes a symbol once with the older of its two fetched rows.
    await strategy.evaluate(SYMBOL, BARS[0])

    for bar, expected in zip(BARS[1:], EXPECTED[1:], strict=True):
        verdicts = {(await strategy.evaluate(SYMBOL, bar)).type for _ in range(3)}
        assert verdicts == {expected}, f"bar {bar['time']} polled inconsistently: {verdicts}"
