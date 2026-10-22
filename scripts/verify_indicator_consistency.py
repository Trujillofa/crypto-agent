#!/usr/bin/env python3
"""Verify that the stored `indicators` rows match the live indicator definition.

The `indicators` table is written by several tools (the live path in
`src/features/computer.py` and the backfills in `scripts/compute_*_indicators.py`).
Because the primary key is `(time, symbol, timeframe)`, a row written by one
tool overwrites the row written by another. This script recomputes the stored
rows from `ohlcv` with the live window and reports, per column, how many rows
agree.

It is a *reporting* tool, not a gate:

- Rows the live path owns must agree. A disagreement there is a live bug.
- Historical rows written by the old backfills are reported, not asserted:
  they were computed under a different window, and after a data revision they
  cannot be reproduced from today's `ohlcv` at all.

Usage:
    uv run python scripts/verify_indicator_consistency.py
    uv run python scripts/verify_indicator_consistency.py --symbol SOLUSDT --timeframe 1h
    uv run python scripts/verify_indicator_consistency.py --recent 40
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

# Same resolver the backfills use, so this script always points at the
# database they would write to.
from scripts.compute_basic_indicators import build_db_config  # noqa: E402
from src.features.technical import INDICATOR_WINDOW_BARS, compute_indicators_for_window
from src.utils.logger import get_logger

logger = get_logger(__name__)

# The columns the live path populates. A backfilled row that leaves any of
# these NULL is a row written by an incomplete writer.
INDICATOR_COLUMNS: tuple[str, ...] = (
    "vwap",
    "ema_8",
    "ema_10",
    "ema_12",
    "ema_14",
    "ema_21",
    "ema_24",
    "ema_26",
    "ema_30",
    "ema_50",
    "ema_200",
    "sma_20",
    "sma_40",
    "sma_50",
    "sma_60",
    "sma_200",
    "rsi_14",
    "rsi_7",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper_dist",
    "bb_lower_dist",
    "atr_14",
    "atr_pct",
    "stoch_k",
    "stoch_d",
    "cci",
    "ema_slope_50",
    "volatility_percentile",
    "atr_percentile",
    "volume_regime",
    "price_vs_weekly",
    "price_vs_monthly",
    "rsi_slope",
    "trend_consistency",
)

# The number of trailing rows the live path owns. The live agent recomputes
# and rewrites exactly ONE row per symbol/timeframe on its cycle: the newest
# OHLCV bar (see src/features/computer.py, which writes `latest_time` and
# nothing else). Every older row is frozen history. Requiring more than one
# row to agree would flag historical rows that the live path has already
# moved past, which is not a live defect.
LIVE_OWNED_ROWS = 1

# A pair is "live-owned" when the live agent still writes to it: its newest
# indicator row is recent (within this many hours of now) and it tracks the
# newest available OHLCV bar. Pairs whose data stopped months ago were
# written once by a backfill and are never touched by the live path, so their
# newest row is not expected to match the live definition.
LIVE_FRESHNESS_HOURS = 6


@dataclass
class ColumnTally:
    """Agreement counters for one column."""

    compared: int = 0
    matching: int = 0
    null_mismatch: int = 0
    value_mismatch: int = 0

    @property
    def matching_pct(self) -> float:
        return (100.0 * self.matching / self.compared) if self.compared else 0.0

    @property
    def clean(self) -> bool:
        return self.compared > 0 and self.value_mismatch == 0 and self.null_mismatch == 0


@dataclass
class PairResult:
    """Verification result for one symbol/timeframe pair."""

    symbol: str
    timeframe: str
    ohlcv_rows: int = 0
    indicator_rows: int = 0
    recomputable: int = 0
    not_recomputable: int = 0
    live_owned: bool = False
    live_rows_checked: int = 0
    live_rows_matching: int = 0
    tallies: dict[str, ColumnTally] = field(default_factory=dict)

    def tally(self, column: str) -> ColumnTally:
        return self.tallies.setdefault(column, ColumnTally())

    def summary(self) -> str:
        live = "n/a"
        if self.live_rows_checked:
            live = f"{self.live_rows_matching}/{self.live_rows_checked}"
        return (
            f"{self.symbol:>10} {self.timeframe:<5} "
            f"ohlcv={self.ohlcv_rows:<7} indicators={self.indicator_rows:<7} "
            f"recomputable={self.recomputable:<7} live_agree={live}"
        )


def _is_close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-9, abs(left) * 1e-9)


async def fetch_pairs(
    conn: asyncpg.Connection,
    symbol: str | None,
    timeframe: str | None,
) -> list[tuple[str, str]]:
    """Return the symbol/timeframe pairs to verify."""
    if symbol and timeframe:
        return [(symbol, timeframe)]

    rows = await conn.fetch(
        """
        SELECT symbol, timeframe
        FROM indicators
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
        """
    )
    pairs = [(row["symbol"], row["timeframe"]) for row in rows]
    if symbol:
        pairs = [pair for pair in pairs if pair[0] == symbol]
    if timeframe:
        pairs = [pair for pair in pairs if pair[1] == timeframe]
    return pairs


async def verify_pair(
    conn: asyncpg.Connection,
    symbol: str,
    timeframe: str,
    recent: int | None,
) -> PairResult:
    """Recompute the stored rows for one pair and compare column by column."""
    result = PairResult(symbol=symbol, timeframe=timeframe)

    ohlcv_rows = await conn.fetch(
        """
        SELECT time, open_price, high_price, low_price, close_price, volume
        FROM ohlcv
        WHERE symbol = $1 AND timeframe = $2
        ORDER BY time ASC
        """,
        symbol,
        timeframe,
    )
    result.ohlcv_rows = len(ohlcv_rows)
    if not ohlcv_rows:
        return result

    indicator_rows = await conn.fetch(
        f"""
        SELECT time, {", ".join(INDICATOR_COLUMNS)}
        FROM indicators
        WHERE symbol = $1 AND timeframe = $2
        ORDER BY time DESC
        {"LIMIT " + str(recent) if recent else ""}
        """,
        symbol,
        timeframe,
    )
    result.indicator_rows = len(indicator_rows)
    if not indicator_rows:
        return result

    index_by_time = {row["time"]: position for position, row in enumerate(ohlcv_rows)}
    newest_time = indicator_rows[0]["time"]

    # A pair is live-owned only if the live agent still writes to it. A pair
    # whose newest indicator row is months behind the newest OHLCV bar was
    # written once by a backfill and is never touched again, so its newest row
    # is not expected to match the live definition.
    newest_ohlcv_time = ohlcv_rows[-1]["time"]
    tracks_ohlcv = newest_time == newest_ohlcv_time
    is_fresh = (datetime.now(UTC) - newest_time) <= timedelta(hours=LIVE_FRESHNESS_HOURS)
    pair_is_live = tracks_ohlcv and is_fresh
    result.live_owned = pair_is_live

    for stored in indicator_rows:
        position = index_by_time.get(stored["time"])
        if position is None or position + 1 < INDICATOR_WINDOW_BARS:
            # No OHLCV behind this bar (or not enough of it): it can never be
            # recomputed from the current data.
            result.not_recomputable += 1
            continue

        result.recomputable += 1
        # The live path owns the newest rows of a live pair.
        is_live_owned = pair_is_live and result.live_rows_checked < LIVE_OWNED_ROWS
        if is_live_owned:
            result.live_rows_checked += 1

        recomputed = compute_indicators_for_window(
            ohlcv_rows, index=position, window=INDICATOR_WINDOW_BARS
        )

        row_matched = True
        for column in INDICATOR_COLUMNS:
            stored_value = stored[column]
            recomputed_value = getattr(recomputed, column)

            if stored_value is None and recomputed_value is None:
                continue

            tally = result.tally(column)
            tally.compared += 1

            if stored_value is None or recomputed_value is None:
                tally.null_mismatch += 1
                row_matched = False
                continue

            if _is_close(float(stored_value), float(recomputed_value)):
                tally.matching += 1
            else:
                tally.value_mismatch += 1
                row_matched = False

        if is_live_owned and row_matched:
            result.live_rows_matching += 1

    return result


async def verify_all(
    symbol: str | None = None,
    timeframe: str | None = None,
    recent: int | None = None,
) -> int:
    """Verify every pair. Returns a process exit code."""
    # Same resolver the backfills use, so this script always points at the
    # database they would write to.
    db_config = build_db_config()

    conn = await asyncpg.connect(
        host=db_config["host"],
        port=db_config["port"],
        database=db_config["name"],
        user=db_config["user"],
        password=db_config["password"],
    )

    try:
        pairs = await fetch_pairs(conn, symbol, timeframe)
        if not pairs:
            logger.error("No symbol/timeframe pairs matched. Nothing to verify.")
            return 1

        logger.info(
            "Verifying %d symbol/timeframe pair(s) with the live window of %d bars",
            len(pairs),
            INDICATOR_WINDOW_BARS,
        )

        results: list[PairResult] = []
        for pair_symbol, pair_timeframe in pairs:
            result = await verify_pair(conn, pair_symbol, pair_timeframe, recent)
            results.append(result)
            logger.info(result.summary())

        has_live_mismatch = _print_report(results)
        # Only a live-owned row disagreeing is a failure. Historical rows
        # written under a different window are reported, not asserted.
        return 1 if has_live_mismatch else 0
    finally:
        await conn.close()


def _print_report(results: Sequence[PairResult]) -> bool:
    """Print the per-column agreement table. Returns True if a live row disagrees."""
    print()
    print("=" * 78)
    print("INDICATOR CONSISTENCY — stored rows vs the live 200-bar definition")
    print("=" * 78)

    live_problems: list[str] = []
    for result in results:
        if result.live_owned and result.live_rows_matching < result.live_rows_checked:
            live_problems.append(
                f"  {result.symbol} {result.timeframe}: "
                f"{result.live_rows_matching}/{result.live_rows_checked} live rows disagree"
            )

    print()
    print("LIVE-OWNED ROWS (must agree; a disagreement here is a live bug)")
    live_pairs = [result for result in results if result.live_owned]
    if not live_pairs:
        print("  (no pair is currently being written by the live path)")
    for result in live_pairs:
        status = "OK" if result.live_rows_matching == result.live_rows_checked else "MISMATCH"
        print(
            f"  {result.symbol:>10} {result.timeframe:<5} "
            f"{result.live_rows_matching}/{result.live_rows_checked}  {status}"
        )

    stale_pairs = [result for result in results if not result.live_owned]
    if stale_pairs:
        print()
        print("NOT LIVE-OWNED (written once by a backfill; reported, not asserted)")
        for result in stale_pairs:
            print(
                f"  {result.symbol:>10} {result.timeframe:<5} "
                f"indicators={result.indicator_rows:<7} recomputable={result.recomputable:<7} "
                f"not_recomputable={result.not_recomputable}"
            )

    print()
    print("ALL ROWS, PER COLUMN (historical rows are reported, not asserted)")
    columns = list(INDICATOR_COLUMNS)
    print(f"  {'column':<24} {'compared':>9} {'match%':>8} {'null-mm':>8} {'value-mm':>9}")
    for column in columns:
        compared = sum(result.tally(column).compared for result in results)
        matching = sum(result.tally(column).matching for result in results)
        null_mm = sum(result.tally(column).null_mismatch for result in results)
        value_mm = sum(result.tally(column).value_mismatch for result in results)
        pct = (100.0 * matching / compared) if compared else 0.0
        print(f"  {column:<24} {compared:>9} {pct:>7.1f}% {null_mm:>8} {value_mm:>9}")

    print()
    if live_problems:
        print("RESULT: LIVE MISMATCH DETECTED")
        for problem in live_problems:
            print(problem)
    else:
        print("RESULT: live-owned rows are consistent with the live window definition.")
    print("=" * 78)
    return bool(live_problems)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify stored indicator rows against the live indicator definition"
    )
    parser.add_argument("--symbol", default=None, help="Only verify this symbol")
    parser.add_argument("--timeframe", default=None, help="Only verify this timeframe")
    parser.add_argument(
        "--recent",
        type=int,
        default=None,
        help="Only check the newest N rows per pair (default: all)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    exit_code = asyncio.run(
        verify_all(symbol=args.symbol, timeframe=args.timeframe, recent=args.recent)
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
