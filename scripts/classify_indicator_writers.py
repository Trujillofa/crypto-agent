#!/usr/bin/env python3
"""Classify every `indicators` row by the writer that produced it.

The `indicators` table has been written by several tools with different
window lengths:

- the live path in `src/features/computer.py` -> 200 bars
- `scripts/compute_historical_indicators.py` -> 751 bars (legacy default)
- `scripts/compute_basic_indicators.py` -> 251 bars (legacy default)

Because the primary key is `(time, symbol, timeframe)`, a row written by one
tool overwrites the row written by another, so the table is a mixture. This
script attributes each row to the window that produced it, which is the
concrete work list for a historical backfill.

Method: reproduce the stored `vwap` at each candidate window length and
attribute the row to the window that matches within a relative tolerance.
The VWAP must be computed the way `src/features/technical.py::_vwap` does it
-- over the *typical price* `(high + low + close) / 3`, not over the close.
Using the close instead will mis-classify nearly every row.

Rows that match no closed-bar window are reported as `other`. They are
mostly rows computed from a *forming* candle: the REST ingest writes
Binance's currently-forming kline, the row is computed from that partial
bar, and the source candle is later revised. Such a row cannot be
reproduced by any closed-bar window.

This is a *reporting* tool. It never writes to the database.

Usage:
    uv run python scripts/classify_indicator_writers.py
    uv run python scripts/classify_indicator_writers.py --symbol SOLUSDT --timeframe 1h
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

# The window lengths that have written to this table.
CANDIDATE_WINDOWS = (200, 251, 751)
RELATIVE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-4
# Pairs with less history than the smallest window cannot be recomputed.
MIN_HISTORY = max(CANDIDATE_WINDOWS)


def rolling_typical_vwap(
    ohlcv: Sequence[asyncpg.Record], windows: Sequence[int]
) -> dict[int, dict[object, float]]:
    """Rolling VWAP over typical price, per window length.

    Uses prefix sums so the cost is O(n) per window instead of O(n*w).
    """
    n = len(ohlcv)
    times = [r["time"] for r in ohlcv]
    pv = [0.0] * (n + 1)
    vol = [0.0] * (n + 1)
    for i, row in enumerate(ohlcv):
        typical = (row["high_price"] + row["low_price"] + row["close_price"]) / 3
        pv[i + 1] = pv[i] + typical * row["volume"]
        vol[i + 1] = vol[i] + row["volume"]
    out: dict[int, dict[object, float]] = {}
    for window in windows:
        values: dict[object, float] = {}
        for i in range(n):
            lo = max(0, i - window + 1)
            denominator = vol[i + 1] - vol[lo]
            if denominator <= 0:
                continue
            values[times[i]] = (pv[i + 1] - pv[lo]) / denominator
        out[window] = values
    return out


def matches(candidate: float, stored: float) -> bool:
    return abs(candidate - stored) <= max(ABSOLUTE_TOLERANCE, abs(stored) * RELATIVE_TOLERANCE)


async def classify_pair(
    conn: asyncpg.Connection, symbol: str, timeframe: str
) -> tuple[dict[str, int], int, int]:
    """Return (counts by window, total stored rows, total ohlcv rows)."""
    ohlcv = await conn.fetch(
        """
        SELECT time, high_price, low_price, close_price, volume
        FROM ohlcv WHERE symbol = $1 AND timeframe = $2 ORDER BY time ASC
        """,
        symbol,
        timeframe,
    )
    stored = {
        r["time"]: r["vwap"]
        for r in await conn.fetch(
            """
            SELECT time, vwap FROM indicators
            WHERE symbol = $1 AND timeframe = $2 AND vwap IS NOT NULL
            """,
            symbol,
            timeframe,
        )
    }
    counts = {str(w): 0 for w in CANDIDATE_WINDOWS}
    counts["other"] = 0
    if len(ohlcv) < MIN_HISTORY:
        return counts, len(stored), len(ohlcv)
    windows = rolling_typical_vwap(ohlcv, CANDIDATE_WINDOWS)
    for time, stored_vwap in stored.items():
        attributed = "other"
        for window in CANDIDATE_WINDOWS:
            candidate = windows[window].get(time)
            if candidate is not None and matches(candidate, stored_vwap):
                attributed = str(window)
                break
        counts[attributed] += 1
    return counts, len(stored), len(ohlcv)


async def main_async(args: argparse.Namespace) -> int:
    conn = await asyncpg.connect(
        host=os.environ.get("POSTGRES_HOST", os.environ.get("DB_HOST", "localhost")),
        port=int(os.environ.get("POSTGRES_PORT", os.environ.get("DB_PORT", "5432"))),
        database=os.environ.get("POSTGRES_DB", os.environ.get("DB_NAME", "marketdata")),
        user=os.environ.get("POSTGRES_USER", os.environ.get("DB_USER", "trading")),
        password=os.environ.get("POSTGRES_PASSWORD", os.environ.get("DB_PASSWORD", "")),
    )
    try:
        if args.symbol and args.timeframe:
            pairs = [{"symbol": args.symbol, "timeframe": args.timeframe}]
        else:
            rows = await conn.fetch(
                """
                SELECT DISTINCT symbol, timeframe FROM indicators
                ORDER BY symbol, timeframe
                """
            )
            pairs = [{"symbol": r["symbol"], "timeframe": r["timeframe"]} for r in rows]

        header = f"{'symbol':10} {'tf':4} {'rows':>7} "
        header += " ".join(f"{'w=' + str(w):>8}" for w in CANDIDATE_WINDOWS)
        header += f" {'other':>8}  dominant"
        print(header)
        totals = {str(w): 0 for w in CANDIDATE_WINDOWS}
        totals["other"] = 0
        for pair in pairs:
            counts, stored_rows, ohlcv_rows = await classify_pair(
                conn, pair["symbol"], pair["timeframe"]
            )
            for key in totals:
                totals[key] += counts[key]
            if ohlcv_rows < MIN_HISTORY:
                print(
                    f"  {pair['symbol']:10} {pair['timeframe']:4} {stored_rows:>7}"
                    f"  (only {ohlcv_rows} ohlcv rows -- not recomputable)"
                )
                continue
            dominant = max((v, k) for k, v in counts.items() if k != "other")
            label = f"w={dominant[1]} ({dominant[0]})" if dominant[0] else "none"
            cells = " ".join(f"{counts[str(w)]:>8}" for w in CANDIDATE_WINDOWS)
            print(
                f"  {pair['symbol']:10} {pair['timeframe']:4} {stored_rows:>7} "
                f"{cells} {counts['other']:>8}  {label}"
            )
        print()
        print(
            "TOTALS  "
            + "  ".join(f"w{w}={totals[str(w)]}" for w in CANDIDATE_WINDOWS)
            + f"  other={totals['other']}"
        )
        print(
            "'other' rows match no closed-bar window -- they were computed from a "
            "forming candle and cannot be reproduced from closed bars."
        )
    finally:
        await conn.close()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="limit to one symbol")
    parser.add_argument("--timeframe", help="limit to one timeframe")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
