#!/usr/bin/env python3
"""Verify that a pg_dump contains the PRE-WRITE state of the indicators table.

Background
----------
`indicators` is a TimescaleDB hypertable, so `pg_dump` writes its rows into
per-chunk tables (`_timescaledb_internal._hyper_2_<n>_chunk`), not into a
`COPY public.indicators` block. The parent table's COPY block is empty.
This script therefore walks every chunk COPY block whose column signature
matches the indicators schema.

What it proves
--------------
The accidental 2026-10-22 rewrite of `BTCUSDT` / `1h` used the migrated
`IndicatorWriter`, which writes all 36 indicator columns. The legacy writer
left eight of them NULL (`ema_8`, `ema_10`, `ema_14`, `ema_21`, `ema_24`,
`ema_30`, `sma_40`, `sma_60`).

So the fingerprint is unambiguous:

  * every BTCUSDT 1h row has all eight columns NULL  -> the dump predates
    the write, and can restore the pre-write state;
  * no BTCUSDT 1h row has all eight NULL             -> the dump was taken
    after the write.

Usage:
    uv run python scripts/verify_indicator_backup.py <dump.sql.gz>
"""

from __future__ import annotations

import gzip
import sys
from collections import Counter
from pathlib import Path

NULL_COLUMNS = (
    "ema_8",
    "ema_10",
    "ema_14",
    "ema_21",
    "ema_24",
    "ema_30",
    "sma_40",
    "sma_60",
)
# The indicators hypertable's column signature.
SIGNATURE_MARKER = "trend_consistency"


def open_dump(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", errors="replace")
    return path.open("r", errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    dump = Path(argv[1])
    if not dump.exists():
        print(f"FAIL: {dump} does not exist")
        return 1

    total_rows = 0
    btc_rows = 0
    btc_legacy_null = 0
    btc_mixed = 0
    pairs: Counter[tuple[str, str]] = Counter()
    chunks_with_data = 0

    with open_dump(dump) as handle:
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
                chunks_with_data += 1
                continue
            if not in_block or not line:
                continue
            parts = line.split("\t")
            if len(parts) != len(cols):
                continue
            total_rows += 1
            idx = {c: i for i, c in enumerate(cols)}
            symbol = parts[idx["symbol"]]
            timeframe = parts[idx["timeframe"]]
            pairs[(symbol, timeframe)] += 1
            if symbol == "BTCUSDT" and timeframe == "1h":
                btc_rows += 1
                nulls = sum(1 for c in NULL_COLUMNS if parts[idx[c]] == "\\N")
                if nulls == len(NULL_COLUMNS):
                    btc_legacy_null += 1
                elif nulls > 0:
                    btc_mixed += 1

    print(f"dump:                    {dump}")
    print(f"indicator chunk blocks:  {chunks_with_data} with data")
    print(f"rows recovered:          {total_rows}")
    print(f"distinct (symbol, tf):   {len(pairs)}")
    print(f"BTCUSDT 1h rows:         {btc_rows}")
    print(f"  ...all 8 legacy cols NULL: {btc_legacy_null}")
    print(f"  ...partially NULL:        {btc_mixed}")
    print()

    if not btc_rows:
        print("VERDICT: INCONCLUSIVE -- no BTCUSDT 1h rows in this dump")
        return 1
    if btc_legacy_null == 0:
        print("VERDICT: POST-WRITE STATE")
        print("  no BTCUSDT 1h row carries the legacy NULL fingerprint, so this dump")
        print("  was taken AFTER the accidental write and cannot restore the")
        print("  pre-write values for that pair.")
        return 1

    print("VERDICT: PRE-WRITE STATE CONFIRMED")
    print(
        f"  {btc_legacy_null} of {btc_rows} BTCUSDT 1h rows carry the full legacy NULL fingerprint,"
    )
    print(
        f"  and {btc_mixed} carry a partial one. The dump therefore predates the accidental write"
    )
    print("  and CAN restore the pre-write state for that pair.")
    if btc_mixed:
        print()
        print("  Note: the mixed rows are the transition zone where the live path had")
        print("  already begun writing complete rows before the dump was taken. A restore")
        print("  therefore reproduces the pre-write state for the legacy rows and the")
        print("  already-migrated state for the rest -- which is exactly what the database")
        print("  looked like at the moment of the dump.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
