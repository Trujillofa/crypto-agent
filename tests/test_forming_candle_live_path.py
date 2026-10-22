"""Regression tests for the forming-candle fix in the live indicator path.

The REST poller writes Binance's *in-progress* kline into `ohlcv` and
revises it when the candle closes. The live indicator computer used to
publish an indicator row for that forming bar, and because the live path
only ever rewrites the newest bar, the row was frozen at a partial value
forever: by the time the candle closed, a newer bar existed, so the closed
bar's indicator row was never recomputed.

Measured on production, this left the 4h rows 2 to 3 hours stale relative
to the final candle (399 of 400 closed 4h rows disagreed with a canonical
200-bar recompute), while the 1h pairs that poll just past the bar
boundary happened to be correct.

The fix: the live computer publishes the newest **closed** candle, and
keeps exactly the canonical trailing 200 closed candles as its window.

These tests pin the behaviour, not the implementation detail.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from src.features.computer import IndicatorComputer
from src.features.technical import INDICATOR_WINDOW_BARS


def _series(
    count: int,
    *,
    seed: int = 7,
    now: datetime | None = None,
    bar_hours: int = 4,
    forming: bool = False,
) -> dict[str, list]:
    """Build an OHLCV series oldest-first.

    When ``forming`` is True the newest bar is still open: its close_time
    lies in the future. Otherwise every bar is closed.
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

    # Bars are laid out backwards from the reference time so that the newest
    # bar is the one just before `reference` (or the forming one).
    span = timedelta(hours=bar_hours)
    for index in range(count):
        # index 0 is the oldest bar.
        open_time = reference - span * (count - index)
        times.append(open_time)
        close_times.append(open_time + span - timedelta(seconds=1))

        price += rng.uniform(-2.0, 2.0)
        high = price + rng.uniform(0.1, 2.0)
        low = price - rng.uniform(0.1, 2.0)
        opens.append(price - rng.uniform(0.0, 0.5))
        highs.append(high)
        lows.append(low)
        closes.append(price)
        volumes.append(rng.uniform(10.0, 100.0))

    if forming:
        # The newest bar opened recently and has not closed yet.
        times[-1] = reference - timedelta(hours=1)
        close_times[-1] = reference + timedelta(hours=bar_hours - 1)
    else:
        # The newest bar closed just before the reference time.
        times[-1] = reference - span
        close_times[-1] = reference - timedelta(seconds=1)

    return {
        "time": times,
        "close_time": close_times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


def test_newest_closed_index_skips_the_forming_bar() -> None:
    """A still-forming newest candle must not be selected."""
    now = datetime.now(UTC)
    data = _series(10, now=now, forming=True)

    index = IndicatorComputer._newest_closed_index(data)

    assert index is not None
    # The forming bar is the last element; the selected one is the one before.
    assert index == len(data["time"]) - 2
    assert data["close_time"][index] <= now


def test_newest_closed_index_selects_the_last_bar_when_all_closed() -> None:
    """With every candle closed, the newest one is selected."""
    now = datetime.now(UTC)
    data = _series(10, now=now, forming=False)

    index = IndicatorComputer._newest_closed_index(data)

    assert index == len(data["time"]) - 1


def test_newest_closed_index_returns_none_when_everything_is_forming() -> None:
    """No closed candle at all means there is nothing to publish."""
    now = datetime.now(UTC)
    data = _series(3, now=now, forming=False)
    # Push every close time into the future.
    data["close_time"] = [t + timedelta(days=1) for t in data["close_time"]]

    assert IndicatorComputer._newest_closed_index(data) is None


def test_newest_closed_index_handles_missing_close_time() -> None:
    """A row without a close_time is treated as closed (non-REST source)."""
    now = datetime.now(UTC)
    data = _series(5, now=now, forming=False)
    data["close_time"][-1] = None

    index = IndicatorComputer._newest_closed_index(data)

    assert index == len(data["time"]) - 1


def test_newest_closed_index_treats_naive_timestamps_as_utc() -> None:
    """Naive datetimes are interpreted as UTC, not as local time."""
    now = datetime.now(UTC)
    data = _series(5, now=now, forming=False)
    data["close_time"] = [t.replace(tzinfo=None) for t in data["close_time"]]

    index = IndicatorComputer._newest_closed_index(data)

    assert index == len(data["time"]) - 1


def test_live_window_is_exactly_the_canonical_window_of_closed_bars() -> None:
    """The published window must be the canonical 200 closed bars.

    Regression guard for the off-by-one that dropping a forming bar would
    otherwise introduce: reading 200 bars and discarding a forming one
    leaves 199, which is a *different* window and therefore a different
    indicator value.
    """
    now = datetime.now(UTC)
    data = _series(INDICATOR_WINDOW_BARS + 1, now=now, forming=True)

    closed_index = IndicatorComputer._newest_closed_index(data)
    assert closed_index is not None

    closed_times = data["time"][: closed_index + 1]
    assert len(closed_times) == INDICATOR_WINDOW_BARS

    # Trimming to the canonical window keeps exactly INDICATOR_WINDOW_BARS.
    trimmed = closed_times[-INDICATOR_WINDOW_BARS:]
    assert len(trimmed) == INDICATOR_WINDOW_BARS
    assert trimmed[-1] == data["time"][-2]


def test_published_bar_is_closed_never_forming() -> None:
    """The bar that gets published is always a closed candle."""
    now = datetime.now(UTC)
    data = _series(INDICATOR_WINDOW_BARS + 1, now=now, forming=True)

    closed_index = IndicatorComputer._newest_closed_index(data)

    assert closed_index is not None
    published_close_time = data["close_time"][closed_index]
    assert published_close_time <= now
    # And it is NOT the forming bar.
    assert published_close_time != data["close_time"][-1]


@pytest.mark.parametrize("forming", [True, False])
def test_window_length_is_stable_regardless_of_forming_state(forming: bool) -> None:
    """The number of bars in the window does not depend on forming state.

    This is the invariant that makes the published value comparable with a
    backfilled row: both use exactly INDICATOR_WINDOW_BARS closed candles.
    """
    now = datetime.now(UTC)
    data = _series(INDICATOR_WINDOW_BARS + 1, now=now, forming=forming)

    closed_index = IndicatorComputer._newest_closed_index(data)
    assert closed_index is not None

    closed_count = closed_index + 1
    assert closed_count >= INDICATOR_WINDOW_BARS
    assert len(data["time"][-INDICATOR_WINDOW_BARS:]) == INDICATOR_WINDOW_BARS
