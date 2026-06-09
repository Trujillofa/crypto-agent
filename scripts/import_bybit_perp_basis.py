#!/usr/bin/env python3
"""Import Bybit linear perp mark/index/premium index klines into perp_basis_metrics.

Uses public v5 endpoints (no auth): mark-price-kline, index-price-kline,
premium-index-price-kline with category=linear.

Reuses align_three_feeds / AlignedBar / upsert etc from the Binance importer
so that basis_bps calculation is bit-identical and cross-venue comparable.
Response normalization produces the same synthetic kline row shape that
_parse_kline_bar expects (open[0], close[4], close_time[6]).

Pagination uses limit=1000 + time windowing + startTime/endTime advancement.
Rate limit backoff on 429 / retCode rate limit codes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.import_perp_basis_metrics import (  # noqa: E402
    INTERVAL_MS,
    AlignedBar,
    align_three_feeds,
    resolve_ohlcv_end,
    upsert_aligned_bars,
)
from scripts.probe_funding_normalization import build_db_config  # noqa: E402
from src.db import close_pool, get_pool, init_pool  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("bybit_perp_basis_import")

BYBIT_API = "https://api.bybit.com"
BATCH_LIMIT = 1000
EXCHANGE_DEFAULT = "bybit"

# Binance-style timeframe name -> Bybit interval string for v5
BYBIT_INTERVAL: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}


def _bybit_interval(timeframe: str) -> str:
    if timeframe not in BYBIT_INTERVAL:
        raise ValueError(f"Unsupported timeframe for Bybit: {timeframe}")
    return BYBIT_INTERVAL[timeframe]


def _normalize_bybit_list(
    raw_list: Sequence[Sequence[object]], interval_ms: int
) -> list[list[object]]:
    """Turn Bybit [startTime, open, high, low, close] (strs) into binance-shaped rows.

    Fabricate close_time using open + (interval_ms-1) to match the convention used
    by test helpers and Binance close times for alignment checks.
    Only [0],[4],[6] are used by align_three_feeds / _parse_kline_bar.
    """
    norm: list[list[object]] = []
    for row in raw_list:
        if row is None or len(row) < 5:
            continue
        try:
            open_ms = int(row[0])
            close_price = float(row[4])
        except (TypeError, ValueError, IndexError):
            continue
        close_ms = open_ms + (interval_ms - 1)
        # Match the 7-elem shape the shared parser expects: [o,?, ?,?, c,?, close]
        norm.append([open_ms, "0", "0", "0", str(close_price), "0", close_ms])
    return norm


async def _fetch_bybit_list(
    session: aiohttp.ClientSession,
    endpoint: str,
    symbol: str,
    bybit_interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list[object]]:
    """Fetch one Bybit v5 kline feed and return its result.list (raw)."""
    url = f"{BYBIT_API}{endpoint}"
    params: dict[str, object] = {
        "category": "linear",
        "symbol": symbol,
        "interval": bybit_interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": BATCH_LIMIT,
    }
    async with session.get(url, params=params) as resp:
        if resp.status == 429:
            logger.warning("Rate limited (429) on %s, backing off", endpoint)
            await asyncio.sleep(1.0)
            return []
        # Some rate limits come as 200 + retCode
        data = await resp.json()
        if isinstance(data, dict):
            rc = data.get("retCode")
            if rc == 10006 or rc == 10029 or rc == 10016:  # common rate limit codes
                logger.warning("Bybit rate limit retCode=%s on %s, backing off", rc, endpoint)
                await asyncio.sleep(1.0)
                return []
            if rc != 0:
                raise RuntimeError(f"Bybit API error on {endpoint}: {data.get('retMsg')} ({rc})")
            result = data.get("result", {})
            if isinstance(result, dict):
                lst = result.get("list", [])
                return lst if isinstance(lst, list) else []
            return []
        # Unexpected shape
        return []


async def fetch_aligned_chunk(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[list[AlignedBar], int]:
    """Fetch a time-bounded chunk from Bybit and align using shared logic."""
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    b_interval = _bybit_interval(timeframe)
    interval_ms = INTERVAL_MS[timeframe]

    mark_raw, index_raw, premium_raw = await asyncio.gather(
        _fetch_bybit_list(
            session, "/v5/market/mark-price-kline", symbol, b_interval, start_ms, end_ms
        ),
        _fetch_bybit_list(
            session, "/v5/market/index-price-kline", symbol, b_interval, start_ms, end_ms
        ),
        _fetch_bybit_list(
            session, "/v5/market/premium-index-price-kline", symbol, b_interval, start_ms, end_ms
        ),
    )

    mark_rows = _normalize_bybit_list(mark_raw, interval_ms)
    index_rows = _normalize_bybit_list(index_raw, interval_ms)
    premium_rows = _normalize_bybit_list(premium_raw, interval_ms)

    return align_three_feeds(mark_rows, index_rows, premium_rows)


async def fetch_all_aligned(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[list[AlignedBar], int]:
    """Paginate over the full [start, end) range using 1000-bar windows."""
    if timeframe not in INTERVAL_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    interval_ms = INTERVAL_MS[timeframe]
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

        await asyncio.sleep(0.15)

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
        "%s: aligned=%d partial_rejected=%d range=%s .. %s",
        symbol,
        len(bars),
        partial,
        start.isoformat(),
        end.isoformat(),
    )
    # Also emit to stdout so dry-run (and CI logs) show progress without requiring
    # the full logging config that get_logger expects in agent runs.
    print(
        f"{symbol}: aligned={len(bars)} partial_rejected={partial} range={start.isoformat()} .. {end.isoformat()}"
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
                if not need_db:
                    logger.error("%s: pass --to for dry-run without DB", symbol)
                    return 1
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
                exchange=args.exchange,
                start=start,
                end=end,
                dry_run=args.dry_run,
            )
            total += inserted
        logger.info("Done: %d bars %s", total, "fetched (dry-run)" if args.dry_run else "upserted")
        print(f"Done: {total} bars {'fetched (dry-run)' if args.dry_run else 'upserted'}")
        return 0
    finally:
        if need_db:
            await close_pool()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Bybit perp basis/premium metrics (public API)."
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--exchange", default=EXCHANGE_DEFAULT)
    parser.add_argument("--from", dest="start_date", default="2024-01-01")
    parser.add_argument("--to", dest="end_date", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and align only; do not write to DB",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
