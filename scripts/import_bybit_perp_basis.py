#!/usr/bin/env python3
"""Import Bybit linear perp mark/index/premium index klines into perp_basis_metrics.

Modeled directly on scripts/import_perp_basis_metrics.py but for Bybit v5 public API.
Reuses the alignment and upsert logic to keep basis_bps computation identical for comparability.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.import_perp_basis_metrics import (  # noqa: E402
    AlignedBar,
    align_three_feeds,
    bar_duration,
    resolve_ohlcv_end,
    upsert_aligned_bars,
)
from scripts.probe_funding_normalization import build_db_config  # noqa: E402
from src.db import close_pool, get_pool, init_pool  # noqa: E402
from src.utils.logger import configure_logger, get_logger  # noqa: E402

logger = get_logger("bybit_perp_basis_import")

BYBIT_BASE = "https://api.bybit.com"
BATCH_LIMIT = 1000  # Bybit max for kline is 1000
EXCHANGE = "bybit"

# Bybit uses minute strings for linear kline interval
BYBIT_INTERVAL = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "D",
    # add more as needed
}


def _bybit_klines_to_binance_style(
    klines: Sequence[Sequence[str]], interval_ms: int
) -> list[list[object]]:
    """Convert Bybit list items to the 7-element format expected by align_three_feeds parser."""
    out = []
    for row in klines:
        start_ms = int(row[0])
        close_ms = start_ms + interval_ms - 1
        close_price = row[4]
        # Binance style used by parser: [openTime, o, h, l, c, v?, closeTime]
        out.append([start_ms, "0", "0", "0", close_price, "0", close_ms])
    return out


async def _fetch_bybit_klines(
    session: aiohttp.ClientSession,
    path: str,
    params: Mapping[str, object],
    max_retries: int = 3,
) -> list[list[str]]:
    url = f"{BYBIT_BASE}{path}"
    for attempt in range(max_retries):
        async with session.get(url, params=dict(params)) as resp:
            if resp.status == 429:
                # Returning [] here would silently skip the window and leave a data gap,
                # so retry with backoff and fail loudly if the limit persists.
                delay = 0.5 * (attempt + 1)
                logger.warning("Rate limited on %s, retrying in %.1fs", path, delay)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            data = await resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error on {path}: {data.get('retMsg')}")
        result = data.get("result", {})
        klines = result.get("list", [])
        if not isinstance(klines, list):
            raise RuntimeError(f"Unexpected Bybit response from {path}")
        # Bybit returns newest first; reverse to oldest first for consistency with pagination logic
        return list(reversed(klines))
    raise RuntimeError(f"Rate limited on {path} after {max_retries} attempts")


async def fetch_aligned_chunk(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[list[AlignedBar], int]:
    interval = BYBIT_INTERVAL.get(timeframe)
    if interval is None:
        raise ValueError(f"Unsupported timeframe for Bybit: {timeframe}")
    interval_ms = bar_duration(timeframe) // timedelta(milliseconds=1)

    base = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": BATCH_LIMIT,
    }

    # Three calls: premium, mark, index
    premium_path = "/v5/market/premium-index-price-kline"
    mark_path = "/v5/market/mark-price-kline"
    index_path = "/v5/market/index-price-kline"

    premium_raw, mark_raw, index_raw = await asyncio.gather(
        _fetch_bybit_klines(session, premium_path, base),
        _fetch_bybit_klines(session, mark_path, base),
        _fetch_bybit_klines(session, index_path, base),
    )

    # Convert to the format the shared align function expects
    mark_b = _bybit_klines_to_binance_style(mark_raw, interval_ms)
    index_b = _bybit_klines_to_binance_style(index_raw, interval_ms)
    premium_b = _bybit_klines_to_binance_style(premium_raw, interval_ms)

    return align_three_feeds(mark_b, index_b, premium_b)


async def fetch_all_aligned(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[list[AlignedBar], int]:
    interval_ms = bar_duration(timeframe) // timedelta(milliseconds=1)
    cursor = start
    by_open: dict[datetime, AlignedBar] = {}
    total_partial = 0

    while cursor < end:
        chunk_end = min(cursor + timedelta(milliseconds=interval_ms * BATCH_LIMIT), end)
        bars, partial = await fetch_aligned_chunk(session, symbol, timeframe, cursor, chunk_end)
        total_partial += partial
        for bar in bars:
            by_open[bar.open_time] = bar

        if not bars:
            cursor = chunk_end
        else:
            last_open_ms = int(bars[-1].open_time.timestamp() * 1000)
            cursor = datetime.fromtimestamp((last_open_ms + interval_ms) / 1000, tz=UTC)

        await asyncio.sleep(0.2)  # polite for Bybit public rate limits

    return sorted(by_open.values(), key=lambda b: b.open_time), total_partial


async def backfill_symbol(
    symbol: str,
    *,
    timeframe: str,
    exchange: str,
    start: datetime,
    end: datetime,
    dry_run: bool,
) -> int:
    async with aiohttp.ClientSession() as session:
        bars, partial = await fetch_all_aligned(session, symbol, timeframe, start, end)

    logger.info(
        "%s: aligned=%d partial_rejected=%d range=%s .. %s (bybit)",
        symbol,
        len(bars),
        partial,
        start.isoformat(),
        end.isoformat(),
    )

    if dry_run or not bars:
        return len(bars)

    pool = get_pool()
    async with pool.acquire() as conn:
        return await upsert_aligned_bars(
            conn,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
        )


async def run(args: argparse.Namespace) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    end_explicit = (
        datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=UTC) if args.end_date else None
    )

    need_db = not (args.dry_run and end_explicit is not None)
    if need_db:
        await init_pool(build_db_config())

    try:
        total = 0
        for symbol in symbols:
            end = end_explicit
            if end is None:
                pool = get_pool()
                async with pool.acquire() as conn:
                    end = await resolve_ohlcv_end(conn, symbol, args.timeframe)
            if end is None:
                logger.error("%s: no ohlcv end time; pass --to", symbol)
                return 1
            if start >= end:
                logger.error("%s: start >= end (%s >= %s)", symbol, start, end)
                return 1

            inserted = await backfill_symbol(
                symbol,
                timeframe=args.timeframe,
                exchange=EXCHANGE,
                start=start,
                end=end,
                dry_run=args.dry_run,
            )
            total += inserted
        logger.info("Done: %d bars %s", total, "fetched (dry-run)" if args.dry_run else "upserted")
        return 0
    finally:
        if need_db:
            await close_pool()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Bybit perp basis/premium metrics (writes exchange=bybit)."
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--from", dest="start_date", default="2024-01-01")
    parser.add_argument("--to", dest="end_date", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and align only; do not write to DB",
    )
    return parser.parse_args()


if __name__ == "__main__":
    configure_logger("INFO")
    raise SystemExit(asyncio.run(run(parse_args())))
