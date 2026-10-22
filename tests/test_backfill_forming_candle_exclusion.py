"""Regression tests: the backfills must not write a row for a FORMING bar.

PR #208 made the live path publish the newest **closed** candle. The
backfills had no equivalent rule: they read every OHLCV row and wrote an
indicator for each, including the bar the REST poller was still filling
in.

That is a direct contradiction. Measured against the live database before
this fix:

  SOLUSDT 1h   FORMING bar 2026-10-22 12:00 (closes 12:59:59)
    backfill would write:  vwap=104.9402  rsi=19.81
    live path publishes:   vwap=104.9487  rsi=17.57   (the 11:00 bar)
  SOLUSDT 4h   FORMING bar 2026-10-22 12:00 (closes 15:59:59)
    backfill would write:  vwap=113.8236  rsi=49.37
    live path publishes:   vwap=113.8179  rsi=46.01   (the 08:00 bar)

Two paths computing different values for the same bar, with different
owners, is exactly the drift this whole effort exists to remove.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.features.technical import (
    INDICATOR_WINDOW_BARS,
    compute_indicators_for_window,
    newest_closed_index,
)


def _rows(count: int, *, forming: bool, now: datetime, bar_hours: int = 1) -> list[dict]:
    """A simple OHLCV series oldest-first, optionally with a forming last bar."""
    span = timedelta(hours=bar_hours)
    rows = []
    price = 100.0
    for index in range(count):
        open_time = now - span * (count - index)
        close_time = open_time + span - timedelta(seconds=1)
        price += 0.5
        rows.append(
            {
                "time": open_time,
                "close_time": close_time,
                "open_price": price - 0.1,
                "high_price": price + 0.3,
                "low_price": price - 0.3,
                "close_price": price,
                "volume": 10.0 + index,
            }
        )
    if forming:
        rows[-1]["time"] = now - timedelta(minutes=5)
        rows[-1]["close_time"] = now + span - timedelta(minutes=5)
    return rows


def test_newest_closed_index_is_shared_between_shapes() -> None:
    """The helper accepts a row sequence and a column-oriented mapping."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = _rows(10, forming=True, now=now)

    from_sequence = newest_closed_index(rows, now=now)
    from_columns = newest_closed_index(
        {
            "time": [r["time"] for r in rows],
            "close_time": [r["close_time"] for r in rows],
        },
        now=now,
    )

    assert from_sequence == from_columns
    assert from_sequence == len(rows) - 2


def test_forming_bar_is_excluded_from_the_computable_range() -> None:
    """The backfill's last computable bar is the newest closed one."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = _rows(INDICATOR_WINDOW_BARS + 5, forming=True, now=now)

    closed_index = newest_closed_index(rows, now=now)
    assert closed_index is not None
    assert closed_index == len(rows) - 2

    # Everything from the closed index on is computable; the forming bar is not.
    closed_rows = rows[: closed_index + 1]
    assert len(closed_rows) == INDICATOR_WINDOW_BARS + 4

    # The forming bar would still "compute" -- the point is that nothing
    # should ask it to.
    forming_indicators = compute_indicators_for_window(
        rows, index=len(rows) - 1, window=INDICATOR_WINDOW_BARS
    )
    closed_indicators = compute_indicators_for_window(
        rows, index=closed_index, window=INDICATOR_WINDOW_BARS
    )
    assert forming_indicators.vwap != closed_indicators.vwap


def test_no_forming_bar_means_every_row_is_computable() -> None:
    """With all candles closed, the newest row is included."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = _rows(INDICATOR_WINDOW_BARS + 5, forming=False, now=now)

    closed_index = newest_closed_index(rows, now=now)
    assert closed_index == len(rows) - 1


def test_missing_close_time_is_treated_as_closed() -> None:
    """Sources that do not track forming state must still work."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = _rows(10, forming=False, now=now)
    rows[-1]["close_time"] = None

    assert newest_closed_index(rows, now=now) == len(rows) - 1


def test_all_forming_returns_none() -> None:
    """No closed candle means there is nothing to write."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = _rows(5, forming=False, now=now)
    for row in rows:
        row["close_time"] = now + timedelta(days=1)

    assert newest_closed_index(rows, now=now) is None


def test_naive_close_times_are_read_as_utc() -> None:
    """Naive datetimes are UTC, not local time."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = _rows(5, forming=False, now=now)
    for row in rows:
        row["close_time"] = row["close_time"].replace(tzinfo=None)

    assert newest_closed_index(rows, now=now) == len(rows) - 1
