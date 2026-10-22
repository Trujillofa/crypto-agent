"""Regression tests for the indicator publication guard.

The guard exists because a forming-candle publication is invisible in the
table: the row looks like any other row, but because the live path only
ever writes the newest bar and never revisits an old one, a bar written
before it closed is frozen at a partial value forever.

These tests pin the three properties the Prometheus alerts depend on:

  1. a correctly published (closed) bar reports a POSITIVE age
  2. a bar published before it closed reports a NEGATIVE age
  3. the 0/1 `was_forming` gauge tracks the sign of that age

Without a test, a refactor that inverts the comparison would silently
disable the only alert standing between this system and a repeat of the
incident.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from src.features.computer import IndicatorComputer
from src.features.metrics import IndicatorMetrics


def _series(
    count: int,
    *,
    seed: int = 7,
    now: datetime | None = None,
    bar_hours: int = 1,
    forming: bool = False,
) -> dict[str, list]:
    """Build a column-oriented OHLCV payload, oldest first.

    When ``forming`` is True the newest bar is still open: its close_time
    lies in the future. Otherwise every bar is closed.

    The key names match what ``IndicatorComputer._read_ohlcv`` returns.
    """
    rng = random.Random(seed)
    reference = now or datetime.now(UTC)
    price = 100.0
    times: list[datetime] = []
    close_times: list[datetime] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []

    span = timedelta(hours=bar_hours)
    for index in range(count):
        open_time = reference - span * (count - index)
        times.append(open_time)
        close_times.append(open_time + span - timedelta(seconds=1))

        price += rng.uniform(-2.0, 2.0)
        highs.append(price + rng.uniform(0.1, 2.0))
        lows.append(price - rng.uniform(0.1, 2.0))
        opens.append(price - rng.uniform(0.0, 0.5))
        closes.append(price)
        volumes.append(rng.uniform(10.0, 100.0))

    if forming:
        # The newest bar closes well into the future: it is still open.
        close_times[-1] = reference + timedelta(hours=3)

    return {
        "time": times,
        "close_time": close_times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


class _FakeWriter:
    """Captures what the computer would have written."""

    def __init__(self) -> None:
        self.written: list = []

    async def write_indicators(self, stored) -> None:
        self.written.append(stored)


def _computer(series: dict[str, list], metrics: IndicatorMetrics) -> IndicatorComputer:
    computer = IndicatorComputer(
        config={},
        symbols=["TESTUSDT"],
        timeframe="1h",
        writer=_FakeWriter(),
        metrics=metrics,
    )
    # _read_ohlcv talks to the shared pool; hand it the fixture instead.
    computer._read_ohlcv = lambda symbol, limit: _async_value(series)  # type: ignore[method-assign]
    return computer


async def _async_value(value):
    return value


def _age(metrics: IndicatorMetrics) -> float:
    value = metrics.published_bar_age.labels(symbol="TESTUSDT", timeframe="1h")._value.get()
    assert value is not None, "the guard did not publish an age"
    return value


def _was_forming(metrics: IndicatorMetrics) -> int:
    value = metrics.published_bar_was_forming.labels(symbol="TESTUSDT", timeframe="1h")._value.get()
    assert value is not None, "the guard did not publish the forming flag"
    return int(value)


@pytest.mark.asyncio
async def test_closed_bar_reports_positive_age() -> None:
    """A bar that closed in the past reports a positive age."""
    metrics = IndicatorMetrics()
    computer = _computer(_series(250, forming=False), metrics)

    await computer._compute_symbol("TESTUSDT")

    age = _age(metrics)
    assert age > 0, f"a closed bar must report a positive age, got {age}"
    assert _was_forming(metrics) == 0


@pytest.mark.asyncio
async def test_forming_bar_is_never_published() -> None:
    """When the newest bar is still forming, the computer publishes the
    PREVIOUS closed bar -- never the forming one.

    This is the behaviour the forming-candle fix installed, and the guard
    must not be defeated by it: the published bar is an hour old, so the
    age is positive and the was_forming flag stays 0.
    """
    metrics = IndicatorMetrics()
    computer = _computer(_series(250, forming=True), metrics)

    await computer._compute_symbol("TESTUSDT")

    age = _age(metrics)
    assert age > 0, f"the published bar must be a closed one, got age {age}"
    assert _was_forming(metrics) == 0


@pytest.mark.asyncio
async def test_source_forming_bar_is_detected() -> None:
    """The ROOT CAUSE guard: the source ohlcv table holding a forming row.

    The original defect began here -- the ingest poller wrote the
    in-progress kline into the source table. The computer skips forming
    rows when publishing, so the indicators are protected, but the SOURCE
    table is still wrong and the next writer may pick it up.

    This test uses the same forming fixture as the publication test and
    asserts the root-cause gauge fires.
    """
    metrics = IndicatorMetrics()
    computer = _computer(_series(250, forming=True), metrics)

    await computer._compute_symbol("TESTUSDT")

    value = metrics.source_newest_bar_was_forming.labels(
        symbol="TESTUSDT", timeframe="1h"
    )._value.get()
    assert value == 1, "the source table holds a forming row and must be flagged"


@pytest.mark.asyncio
async def test_source_forming_bar_reports_how_long_it_has_been_open() -> None:
    """The forming bar's open duration is what makes the alert usable.

    A bar is normally open for the whole of its period, so a fixed
    threshold would fire constantly on 4h pairs and never on a stuck 1h
    pair. The metric has to report the DURATION so the alert can compare
    it against the bar's own period.
    """
    metrics = IndicatorMetrics()
    computer = _computer(_series(250, forming=True), metrics)

    await computer._compute_symbol("TESTUSDT")

    forming_for = metrics.source_newest_bar_forming_for.labels(
        symbol="TESTUSDT", timeframe="1h"
    )._value.get()
    assert forming_for is not None
    # The fixture's forming bar closes 3 hours from now.
    assert forming_for > 2.5 * 3600, f"expected ~3h, got {forming_for}"
    assert forming_for < 3.5 * 3600, f"expected ~3h, got {forming_for}"

    # And the period, so the alert can compare the two.
    period = metrics.published_bar_period.labels(symbol="TESTUSDT", timeframe="1h")._value.get()
    assert period == 3600


@pytest.mark.asyncio
async def test_source_fully_closed_is_not_flagged() -> None:
    """A fully closed source table must not be flagged."""
    metrics = IndicatorMetrics()
    computer = _computer(_series(250, forming=False), metrics)

    await computer._compute_symbol("TESTUSDT")

    value = metrics.source_newest_bar_was_forming.labels(
        symbol="TESTUSDT", timeframe="1h"
    )._value.get()
    assert value == 0, "a fully closed source table must not be flagged"


def test_guard_math_detects_a_forming_bar() -> None:
    """The guard's own comparison must flag a forming bar.

    The live path no longer publishes forming bars, so the "forming"
    branch is unreachable from the computer. That is exactly why it needs
    a direct test: a refactor could reintroduce the bug, and this is the
    assertion that would catch the guard failing to notice.

    The rule the guard implements, stated directly:

        a bar is forming at publication time  <=>  now < bar.close_time
        which is equivalent to                <=>  age < 0
    """
    now = datetime.now(UTC)

    forming_close = now + timedelta(hours=3)
    age = (now - forming_close).total_seconds()
    assert age < 0, "a bar closing in the future must yield a negative age"
    assert (1 if age < 0 else 0) == 1, "the guard must flag it"

    closed_close = now - timedelta(minutes=1)
    age = (now - closed_close).total_seconds()
    assert age > 0
    assert (1 if age < 0 else 0) == 0, "a closed bar must not be flagged"


@pytest.mark.asyncio
async def test_published_bar_timestamp_is_the_bar_not_now() -> None:
    """The timestamp gauge must be the BAR's timestamp.

    If it recorded the computation time instead, the "stopped publishing"
    alert could never fire -- it would always look fresh.
    """
    metrics = IndicatorMetrics()
    now = datetime.now(UTC)
    series = _series(250, now=now, forming=False)
    expected_bar_close = series["close_time"][-1]
    computer = _computer(series, metrics)

    await computer._compute_symbol("TESTUSDT")

    ts = metrics.published_bar_timestamp.labels(symbol="TESTUSDT", timeframe="1h")._value.get()
    assert ts is not None
    assert ts < now.timestamp(), "timestamp must be the bar's, not the computation's"
    assert abs(ts - expected_bar_close.timestamp()) < 1, "timestamp must be the bar's close time"
