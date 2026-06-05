#!/usr/bin/env python3
"""Import Binance USDT-M mark/index/premium index klines into perp_basis_metrics.

Aligns three feeds on open_time only; rejects partial bars (no forward-fill).
Uses ON CONFLICT DO UPDATE for deterministic re-backfill.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_funding_normalization import build_db_config  # noqa: E402
from src.db import close_pool, get_pool, init_pool  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("perp_basis_import")

BINANCE_FAPI = "https://fapi.binance.com"
BATCH_LIMIT = 1500
EXCHANGE_DEFAULT = "binance_usdm"

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
}


@dataclass(frozen=True)
class KlineBar:
    open_time: datetime
    close_time: datetime
    close_price: float


@dataclass(frozen=True)
class AlignedBar:
    open_time: datetime
    close_time: datetime
    mark_price: float
    index_price: float
    premium_index: float
    basis_bps: float


def bar_duration(timeframe: str) -> timedelta:
    ms = INTERVAL_MS.get(timeframe)
    if ms is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return timedelta(milliseconds=ms)


def _parse_kline_bar(kline: Sequence[object]) -> KlineBar:
    open_ms = int(kline[0])
    close_ms = int(kline[6])
    close_price = float(kline[4])
    return KlineBar(
        open_time=datetime.fromtimestamp(open_ms / 1000, tz=UTC),
        close_time=datetime.fromtimestamp(close_ms / 1000, tz=UTC),
        close_price=close_price,
    )


def _bars_by_open_time(klines: Sequence[Sequence[object]]) -> dict[datetime, KlineBar]:
    bars: dict[datetime, KlineBar] = {}
    for row in klines:
        bar = _parse_kline_bar(row)
        bars[bar.open_time] = bar
    return bars


def align_three_feeds(
    mark_klines: Sequence[Sequence[object]],
    index_klines: Sequence[Sequence[object]],
    premium_klines: Sequence[Sequence[object]],
) -> tuple[list[AlignedBar], int]:
    """Return aligned bars and count of open_times dropped due to missing feed."""
    mark = _bars_by_open_time(mark_klines)
    index = _bars_by_open_time(index_klines)
    premium = _bars_by_open_time(premium_klines)

    all_times = set(mark) | set(index) | set(premium)
    common_times = sorted(set(mark) & set(index) & set(premium))
    partial = len(all_times) - len(common_times)

    aligned: list[AlignedBar] = []
    for open_time in common_times:
        m = mark[open_time]
        i = index[open_time]
        p = premium[open_time]
        if m.close_time != i.close_time or m.close_time != p.close_time:
            partial += 1
            continue
        if i.close_price <= 0:
            partial += 1
            continue
        basis_bps = (m.close_price - i.close_price) / i.close_price * 10_000.0
        aligned.append(
            AlignedBar(
                open_time=open_time,
                close_time=m.close_time,
                mark_price=m.close_price,
                index_price=i.close_price,
                premium_index=p.close_price,
                basis_bps=basis_bps,
            )
        )
    return aligned, partial


async def _fetch_klines(
    session: aiohttp.ClientSession,
    path: str,
    params: Mapping[str, object],
) -> list[list[object]]:
    url = f"{BINANCE_FAPI}{path}"
    async with session.get(url, params=dict(params)) as resp:
        if resp.status == 429:
            logger.warning("Rate limited on %s, backing off", path)
            await asyncio.sleep(1.0)
            return []
        resp.raise_for_status()
        data = await resp.json()
        if isinstance(data, dict) and "code" in data:
            raise RuntimeError(f"Binance API error on {path}: {data}")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected response from {path}: {type(data)}")
        return data


async def fetch_aligned_chunk(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[list[AlignedBar], int]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    base = {
        "interval": timeframe,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": BATCH_LIMIT,
    }
    mark_params = {**base, "symbol": symbol}
    premium_params = {**base, "symbol": symbol}
    index_params = {**base, "pair": symbol}

    mark_raw, index_raw, premium_raw = await asyncio.gather(
        _fetch_klines(session, "/fapi/v1/markPriceKlines", mark_params),
        _fetch_klines(session, "/fapi/v1/indexPriceKlines", index_params),
        _fetch_klines(session, "/fapi/v1/premiumIndexKlines", premium_params),
    )
    return align_three_feeds(mark_raw, index_raw, premium_raw)


async def fetch_all_aligned(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[list[AlignedBar], int]:
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

        await asyncio.sleep(0.1)

    return sorted(by_open.values(), key=lambda b: b.open_time), total_partial


async def upsert_aligned_bars(
    conn,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[AlignedBar],
) -> int:
    if not bars:
        return 0

    query = """
        INSERT INTO perp_basis_metrics (
            time, close_time, exchange, symbol, timeframe,
            mark_price, index_price, premium_index, basis_bps
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (time, exchange, symbol, timeframe) DO UPDATE SET
            close_time = EXCLUDED.close_time,
            mark_price = EXCLUDED.mark_price,
            index_price = EXCLUDED.index_price,
            premium_index = EXCLUDED.premium_index,
            basis_bps = EXCLUDED.basis_bps
    """
    async with conn.transaction():
        for bar in bars:
            await conn.execute(
                query,
                bar.open_time,
                bar.close_time,
                exchange,
                symbol,
                timeframe,
                bar.mark_price,
                bar.index_price,
                bar.premium_index,
                bar.basis_bps,
            )
    return len(bars)


async def resolve_ohlcv_end(conn, symbol: str, timeframe: str) -> datetime | None:
    return await conn.fetchval(
        """
        SELECT MAX(time) FROM ohlcv
        WHERE symbol = $1 AND timeframe = $2
        """,
        symbol,
        timeframe,
    )


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
        return 0
    finally:
        if need_db:
            await close_pool()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill perp basis/premium metrics.")
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. BTCUSDT")
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
