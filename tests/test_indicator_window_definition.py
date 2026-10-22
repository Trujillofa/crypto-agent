"""Regression tests for the shared indicator-window definition.

The `indicators` table has a single primary key `(time, symbol, timeframe)`,
so every writer of it must agree on *what an indicator value is* for a given
bar. These tests pin that agreement:

- the backfill helper reproduces the live path's value exactly, and
- the historical backfill scripts use the live window and slice semantics
  rather than a window of their own.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.compute_1h_indicators import parse_args as parse_1h_args
from scripts.compute_basic_indicators import parse_args
from scripts.compute_historical_indicators import parse_args as parse_historical_args
from src.features.technical import (
    INDICATOR_WINDOW_BARS,
    compute_indicators,
    compute_indicators_for_window,
)


def _make_rows(count: int, seed: int = 42) -> list[dict[str, object]]:
    """Build deterministic OHLCV rows."""
    rng = random.Random(seed)
    base_time = datetime(2025, 1, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    price = 100.0
    for index in range(count):
        price += rng.uniform(-2.0, 2.0)
        high = price + rng.uniform(0.1, 2.0)
        low = price - rng.uniform(0.1, 2.0)
        rows.append(
            {
                "time": base_time + timedelta(hours=index),
                "open_price": price,
                "high_price": high,
                "low_price": low,
                "close_price": price + rng.uniform(-0.5, 0.5),
                "volume": rng.uniform(100.0, 5000.0),
            }
        )
    return rows


def _series(rows: list[dict[str, object]]) -> dict[str, list[float]]:
    return {
        "open": [float(row["open_price"]) for row in rows],  # type: ignore[arg-type]
        "high": [float(row["high_price"]) for row in rows],  # type: ignore[arg-type]
        "low": [float(row["low_price"]) for row in rows],  # type: ignore[arg-type]
        "close": [float(row["close_price"]) for row in rows],  # type: ignore[arg-type]
        "volume": [float(row["volume"]) for row in rows],  # type: ignore[arg-type]
    }


def test_window_constant_matches_the_live_path() -> None:
    """The live path reads `limit=INDICATOR_WINDOW_BARS` bars."""
    assert INDICATOR_WINDOW_BARS == 200


def test_helper_reproduces_the_live_computation_exactly() -> None:
    """A backfilled row must equal the live row for the same bar.

    The live path computes over the most recent `INDICATOR_WINDOW_BARS` bars
    ending at the bar in question. The helper must slice the same bars.
    """
    rows = _make_rows(INDICATOR_WINDOW_BARS + 50)
    index = len(rows) - 10

    live = compute_indicators(_series(rows[index + 1 - INDICATOR_WINDOW_BARS : index + 1]))
    backfilled = compute_indicators_for_window(rows, index=index)

    assert backfilled.vwap == pytest.approx(live.vwap, rel=1e-12)
    assert backfilled.ema_50 == pytest.approx(live.ema_50, rel=1e-12)
    assert backfilled.ema_200 == pytest.approx(live.ema_200, rel=1e-12)
    assert backfilled.rsi_14 == pytest.approx(live.rsi_14, rel=1e-12)
    assert backfilled.atr_14 == pytest.approx(live.atr_14, rel=1e-12)
    assert backfilled.trend_consistency == pytest.approx(live.trend_consistency, rel=1e-12)


def test_helper_does_not_use_the_off_by_one_slice() -> None:
    """The old backfill slice was `rows[i - lookback : i + 1]` = lookback + 1 bars.

    That produced a different value for the same bar even when the window
    length was set equal to the live path's. This test pins the difference.
    """
    rows = _make_rows(INDICATOR_WINDOW_BARS + 50)
    index = len(rows) - 10

    # The old, off-by-one slice: INDICATOR_WINDOW_BARS + 1 bars.
    off_by_one = compute_indicators(_series(rows[index - INDICATOR_WINDOW_BARS : index + 1]))
    correct = compute_indicators_for_window(rows, index=index)

    # They must differ, otherwise the off-by-one was never the problem.
    assert off_by_one.vwap != pytest.approx(correct.vwap, rel=1e-9)
    assert off_by_one.ema_200 != pytest.approx(correct.ema_200, rel=1e-9)


def test_helper_requires_a_full_window() -> None:
    rows = _make_rows(INDICATOR_WINDOW_BARS - 1)

    with pytest.raises(ValueError, match="Not enough data"):
        compute_indicators_for_window(rows, index=len(rows) - 1)


def test_helper_rejects_out_of_range_index() -> None:
    rows = _make_rows(INDICATOR_WINDOW_BARS)

    with pytest.raises(ValueError, match="out of range"):
        compute_indicators_for_window(rows, index=len(rows))


def test_helper_populates_every_column() -> None:
    """A backfilled row must not leave columns NULL that the live path fills.

    The historical writers used to write a subset of columns, which left a
    distinctive NULL fingerprint in the table.
    """
    rows = _make_rows(INDICATOR_WINDOW_BARS)
    indicators = compute_indicators_for_window(rows, index=INDICATOR_WINDOW_BARS - 1)

    assert indicators.sma_40 is not None
    assert indicators.sma_60 is not None
    assert indicators.ema_8 is not None
    assert indicators.ema_10 is not None
    assert indicators.ema_14 is not None
    assert indicators.ema_21 is not None
    assert indicators.ema_24 is not None
    assert indicators.ema_30 is not None
    assert indicators.vwap is not None
    assert indicators.ema_slope_50 is not None
    assert indicators.volatility_percentile is not None
    assert indicators.atr_percentile is not None
    assert indicators.volume_regime is not None
    assert indicators.price_vs_weekly is not None
    assert indicators.price_vs_monthly is not None
    assert indicators.rsi_slope is not None
    assert indicators.trend_consistency is not None


def test_historical_script_defaults_to_the_live_window() -> None:
    """The historical backfill must not default to a window of its own."""
    args = parse_historical_args(["--symbol", "BTCUSDT", "--timeframe", "4h"])

    assert args.lookback == INDICATOR_WINDOW_BARS


def test_basic_script_defaults_to_the_live_window() -> None:
    args = parse_args(["--symbol", "BTCUSDT", "--timeframe", "4h"])

    assert args.symbol == "BTCUSDT"
    assert args.timeframe == "4h"
    assert args.lookback == INDICATOR_WINDOW_BARS


def test_historical_script_supports_dry_run() -> None:
    """A data rewrite must be previewable before it touches the table."""
    args = parse_historical_args(["--symbol", "BTCUSDT", "--timeframe", "4h"])

    assert args.dry_run is False
    assert parse_historical_args(["--symbol", "BTCUSDT", "--timeframe", "4h", "--dry-run"]).dry_run


def test_basic_script_supports_dry_run() -> None:
    """A data rewrite must be previewable before it touches the table."""
    assert parse_args(["--symbol", "BTCUSDT", "--timeframe", "4h"]).dry_run is False
    assert parse_args(["--symbol", "BTCUSDT", "--timeframe", "4h", "--dry-run"]).dry_run is True


def test_1h_script_supports_dry_run() -> None:
    """A data rewrite must be previewable before it touches the table.

    This script writes straight to the `indicators` table with no other
    safeguard, so a dry-run flag is the only thing standing between a
    preview and a production rewrite.
    """
    assert parse_1h_args([]).dry_run is False
    assert parse_1h_args(["--dry-run"]).dry_run is True


@pytest.mark.parametrize("script_name", ["compute_1h_indicators.py"])
def test_backfill_script_write_is_guarded_by_dry_run(script_name: str) -> None:
    """Every backfill must skip the write when `--dry-run` is set.

    Guards the exact failure mode: a backfill invoked with a dry-run flag
    that still writes to the table.
    """
    source = Path(__file__).resolve().parent.parent / "scripts" / script_name
    text = source.read_text()

    assert "if not dry_run:" in text, f"{script_name} writes without a dry-run guard"
    assert "--dry-run" in text, f"{script_name} has no --dry-run flag"
