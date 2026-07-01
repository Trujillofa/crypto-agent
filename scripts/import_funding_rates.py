#!/usr/bin/env python3
"""Import historical funding rates from Binance API.

Usage:
    # Backfill all symbols for last 90 days (max available)
    python scripts/import_funding_rates.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT

    # Backfill specific symbol with date range
    python scripts/import_funding_rates.py --symbols AVAXUSDT --from 2024-01-01 --to 2024-12-31

Environment:
    DATABASE_URL: PostgreSQL connection string
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp
import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import get_logger

logger = get_logger("funding_rate_import")

BINANCE_API_BASE = "https://fapi.binance.com"


async def fetch_funding_rates(
    session: aiohttp.ClientSession,
    symbol: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict]:
    """Fetch funding rates from Binance API."""
    url = f"{BINANCE_API_BASE}/fapi/v1/fundingRate"

    params = {
        "symbol": symbol,
        "startTime": int(start_time.timestamp() * 1000),
        "endTime": int(end_time.timestamp() * 1000),
        "limit": 1000,
    }

    async with session.get(url, params=params) as resp:
        if resp.status == 429:
            logger.warning("Rate limited, backing off...")
            await asyncio.sleep(1)
            return []

        resp.raise_for_status()
        data = await resp.json()

        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "code" in data:
            logger.error(f"API error: {data}")
            return []
        else:
            return [data] if data else []


async def insert_funding_rates(
    conn: asyncpg.Connection,
    rates: list[dict],
) -> int:
    """Insert funding rates into database, skipping duplicates."""
    if not rates:
        return 0

    inserted = 0
    async with conn.transaction():
        for rate in rates:
            symbol = rate.get("symbol")
            funding_time_ms = rate.get("fundingTime")
            funding_rate = float(rate.get("fundingRate", 0))
            raw_mark_price = rate.get("markPrice")
            mark_price = float(raw_mark_price) if raw_mark_price not in (None, "") else None

            if not all([symbol, funding_time_ms is not None]):
                continue

            funding_time = datetime.fromtimestamp(funding_time_ms / 1000, tz=UTC)

            try:
                await conn.execute(
                    """
                    INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (symbol, funding_time) DO NOTHING
                    """,
                    symbol,
                    funding_time,
                    funding_rate,
                    mark_price,
                )
                inserted += 1
            except Exception as e:
                logger.error(f"Failed to insert {symbol} @ {funding_time}: {e}")

    return inserted


async def backfill_symbol(
    pool: asyncpg.Pool,
    session: aiohttp.ClientSession,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
) -> int:
    """Backfill funding rates for a symbol over date range."""
    logger.info(f"Backfilling {symbol} from {start_date.date()} to {end_date.date()}")

    total_inserted = 0
    current_start = start_date

    while current_start < end_date:
        current_end = min(current_start + timedelta(days=90), end_date)

        rates = await fetch_funding_rates(session, symbol, current_start, current_end)

        if rates:
            async with pool.acquire() as conn:
                inserted = await insert_funding_rates(conn, rates)
                total_inserted += inserted
                logger.info(f"  Inserted {inserted}/{len(rates)} rates for {symbol}")
        else:
            logger.warning(f"  No data returned for {symbol} @ {current_start.date()}")

        current_start = current_end
        await asyncio.sleep(0.1)  # Rate limit protection

    return total_inserted


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbols",
        type=str,
        required=True,
        help="Comma-separated list of symbols (e.g., BTCUSDT,ETHUSDT)",
    )
    parser.add_argument(
        "--from",
        dest="start_date",
        type=str,
        default=None,
        help="Start date (YYYY-MM-DD, default: 90 days ago)",
    )
    parser.add_argument(
        "--to",
        dest="end_date",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch only, don't insert",
    )
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        end_date = datetime.now(UTC)

    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        start_date = end_date - timedelta(days=90)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        return 1

    pool = None
    try:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)

        if args.dry_run:
            logger.info("DRY RUN: Would backfill the following:")
            for symbol in symbols:
                logger.info(f"  {symbol}: {start_date.date()} to {end_date.date()}")
            return 0

        total_rates = 0
        async with aiohttp.ClientSession() as session:
            for symbol in symbols:
                inserted = await backfill_symbol(pool, session, symbol, start_date, end_date)
                total_rates += inserted
                logger.info(f"Completed {symbol}: {inserted} rates inserted")

        logger.info(f"Total: {total_rates} funding rates inserted")
        return 0

    except Exception as e:
        logger.error(f"Import failed: {e}")
        return 1
    finally:
        if pool:
            await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
