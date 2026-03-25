#!/usr/bin/env python3
"""Quick historical data download for backtesting."""

import asyncio
import os
from datetime import datetime

import aiohttp
import asyncpg
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)


async def fetch_klines(session, symbol, interval, start_time, end_time, limit=1000):
    """Fetch klines from Binance API."""
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit,
    }

    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def download_all_klines(symbol, interval, start, end):
    """Download all klines for a date range."""
    start_ts = int(pd.to_datetime(start).timestamp() * 1000)
    end_ts = int(pd.to_datetime(end).timestamp() * 1000)

    all_klines = []

    async with aiohttp.ClientSession() as session:
        while start_ts < end_ts:
            klines = await fetch_klines(session, symbol, interval, start_ts, end_ts)
            if not klines:
                break
            all_klines.extend(klines)
            start_ts = klines[-1][0] + 1
            logger.info(f"Downloaded {len(klines)} candles, total: {len(all_klines)}")
            await asyncio.sleep(0.1)  # Rate limiting

    return all_klines


async def save_to_db(pool, symbol, timeframe, klines):
    """Save klines to database."""
    async with pool.acquire() as conn:
        for k in klines:
            await conn.execute(
                """
                INSERT INTO ohlcv (time, close_time, symbol, timeframe, open_price, high_price, low_price, close_price, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                    close_time = EXCLUDED.close_time,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume
                """,
                datetime.fromtimestamp(k[0] / 1000),
                datetime.fromtimestamp(k[6] / 1000),
                symbol,
                timeframe,
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5]),
            )
    logger.info(f"Saved {len(klines)} candles to database")


async def main():
    symbol = "BTCUSDT"
    timeframe = "1h"
    start = "2024-01-01"
    end = "2025-03-15"

    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 15432)),
        "database": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", "change_me"),
    }

    logger.info(f"Downloading {symbol} {timeframe} from {start} to {end}...")
    klines = await download_all_klines(symbol, timeframe, start, end)

    if not klines:
        logger.error("No data downloaded")
        return

    logger.info("Connecting to database...")
    pool = await asyncpg.create_pool(
        host=db_config["host"],
        port=db_config["port"],
        database=db_config["database"],
        user=db_config["user"],
        password=db_config["password"],
    )

    try:
        await save_to_db(pool, symbol, timeframe, klines)
    finally:
        await pool.close()

    logger.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
