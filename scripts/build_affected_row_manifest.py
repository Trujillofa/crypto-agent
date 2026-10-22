#!/usr/bin/env python3
"""Build an exact affected-row manifest by diffing a pg_dump against the live DB.

The accidental 2026-10-22 write upserted `BTCUSDT` / `1h` indicator rows
with the migrated `IndicatorWriter` (all 36 value columns) over rows the
legacy writer had produced (31 columns, eight of them NULL).

To say exactly which rows changed -- and to prove a restore path -- this
script:

1. reads the `indicators` rows for a pair out of a `pg_dump` (TimescaleDB
   stores them in per-chunk tables, so the `COPY public.indicators` block
   is empty and the chunk blocks must be scanned);
2. reads the same rows from the live database;
3. compares them value by value.

Output: the set of keys that differ, the columns that differ, and the
before/after values for a sample.

Read-only: nothing is written to the database.

Usage:
    uv run python scripts/build_affected_row_manifest.py \
        --dump backups/preserved/marketdata_20261022_030001.sql.gz \
        --symbol BTCUSDT --timeframe 1h
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import os
from collections import Counter
from pathlib import Path

import asyncpg

SIGNATURE_MARKER = "trend_consistency"
VALUE_COLUMNS = (
    "rsi_14",
    "rsi_7",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper_dist",
    "bb_lower_dist",
    "atr_14",
    "atr_pct",
    "ema_12",
    "ema_26",
    "ema_50",
    "ema_200",
    "sma_20",
    "sma_50",
    "sma_200",
    "vwap",
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
    "ema_8",
    "ema_10",
    "ema_14",
    "ema_21",
    "ema_24",
    "ema_30",
    "sma_40",
    "sma_60",
)


def load_dump_rows(path: Path, symbol: str, timeframe: str) -> dict[str, dict[str, str]]:
    """Return {time: {column: value_as_text}} for one pair from a pg_dump."""
    rows: dict[str, dict[str, str]] = {}
    with gzip.open(path, "rt", errors="replace") as handle:
        cols: list[str] = []
        in_block = False
        for line in handle:
            line = line.rstrip("\n")
            if line.startswith("COPY ") and SIGNATURE_MARKER in line:
                cols = [
                    c.strip().strip('"') for c in line.split("(", 1)[1].rsplit(")", 1)[0].split(",")
                ]
                in_block = True
                continue
            if in_block and line == "\\.":
                in_block = False
                continue
            if not in_block or not line:
                continue
            parts = line.split("\t")
            if len(parts) != len(cols):
                continue
            idx = {c: i for i, c in enumerate(cols)}
            if parts[idx["symbol"]] != symbol or parts[idx["timeframe"]] != timeframe:
                continue
            key = key_of(parts[idx["time"]])
            rows[key] = {c: parts[idx[c]] for c in cols}
    return rows


def norm(value: str) -> str | None:
    return None if value == "\\N" else value


def key_of(value: str) -> str:
    """Normalise a timestamp to 'YYYY-MM-DD HH:MM:SS'.

    The dump writes `2024-01-09 07:00:00+00`; asyncpg returns
    `2024-01-09T07:00:00+00:00`. Both must map to the same key.
    """
    text = value.replace("T", " ").strip()
    for suffix in ("+00:00", "+00"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip()


async def main_async(args: argparse.Namespace) -> int:
    dump_rows = load_dump_rows(Path(args.dump), args.symbol, args.timeframe)
    print(f"dump rows for {args.symbol} {args.timeframe}: {len(dump_rows)}")

    conn = await asyncpg.connect(
        host=os.environ.get("POSTGRES_HOST", os.environ.get("DB_HOST", "localhost")),
        port=int(os.environ.get("POSTGRES_PORT", os.environ.get("DB_PORT", "5432"))),
        database=os.environ.get("POSTGRES_DB", os.environ.get("DB_NAME", "marketdata")),
        user=os.environ.get("POSTGRES_USER", os.environ.get("DB_USER", "trading")),
        password=os.environ.get("POSTGRES_PASSWORD", os.environ.get("DB_PASSWORD", "")),
    )
    try:
        live = {
            key_of(r["time"].isoformat()): {c: r[c] for c in VALUE_COLUMNS}
            for r in await conn.fetch(
                """
                SELECT time, """
                + ", ".join(VALUE_COLUMNS)
                + """
                FROM indicators WHERE symbol = $1 AND timeframe = $2
                """,
                args.symbol,
                args.timeframe,
            )
        }
    finally:
        await conn.close()

    print(f"live rows for {args.symbol} {args.timeframe}: {len(live)}")
    print()

    changed: list[str] = []
    unchanged = 0
    only_dump: list[str] = []
    only_live: list[str] = []
    col_counts: Counter[str] = Counter()
    samples: list[tuple[str, str, str, str]] = []

    for key, drow in dump_rows.items():
        lrow = live.get(key_of(key))
        if lrow is None:
            only_dump.append(key)
            continue
        diff_cols = []
        for col in VALUE_COLUMNS:
            dv = norm(drow.get(col, "\\N"))
            lv = norm(str(lrow.get(col)) if lrow.get(col) is not None else "\\N")
            if dv is None and lv is None:
                continue
            if dv is None or lv is None:
                diff_cols.append(col)
                continue
            try:
                if abs(float(dv) - float(lv)) > max(1e-9, abs(float(lv)) * 1e-9):
                    diff_cols.append(col)
            except ValueError:
                if dv != lv:
                    diff_cols.append(col)
        if diff_cols:
            changed.append(key)
            for c in diff_cols:
                col_counts[c] += 1
            if len(samples) < args.samples:
                c0 = diff_cols[0]
                samples.append((key, c0, str(drow.get(c0)), str(lrow.get(c0))))
        else:
            unchanged += 1

    for key in live:
        if key not in dump_rows:
            only_live.append(key)

    print("=== manifest ===")
    print(f"  rows in dump but not live: {len(only_dump)}")
    print(f"  rows in live but not dump: {len(only_live)}")
    print(f"  rows identical:            {unchanged}")
    print(f"  rows CHANGED:              {len(changed)}")
    print()
    if changed:
        print("  columns that changed (count of affected rows):")
        for col, n in col_counts.most_common():
            print(f"    {col:24} {n}")
        print()
        print("  samples (dump value -> live value):")
        for key, col, dv, lv in samples:
            print(f"    {key[:19]}  {col}: {dv} -> {lv}")
    if only_dump:
        print(f"  earliest dump-only key: {min(only_dump)[:19]}")
    if only_live:
        print(f"  earliest live-only key: {min(only_live)[:19]}")

    if args.out:
        Path(args.out).write_text(
            "\n".join(sorted(changed)) + ("\n" if changed else ""), encoding="utf-8"
        )
        print(f"\n  changed keys written to {args.out}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True, help="path to a pg_dump .sql.gz")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--out", help="write the changed keys to this file")
    parser.add_argument("--samples", type=int, default=5)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
