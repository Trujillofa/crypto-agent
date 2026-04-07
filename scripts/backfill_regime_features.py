#!/usr/bin/env python3
"""Backfill regime indicator columns for existing indicator rows.

This script fills in NULL ema_slope_50, volatility_percentile, atr_percentile,
volume_regime, price_vs_weekly, price_vs_monthly, rsi_slope, trend_consistency
without recomputing all standard indicators.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import argparse
import asyncio
import time

import asyncpg

from src.features.technical import compute_indicators
from src.utils.logger import get_logger

logger = get_logger(__name__)

UPDATE_SQL = """
    UPDATE indicators SET
        ema_slope_50 = $1,
        volatility_percentile = $2,
        atr_percentile = $3,
        volume_regime = $4,
        price_vs_weekly = $5,
        price_vs_monthly = $6,
        rsi_slope = $7,
        trend_consistency = $8
    WHERE time = $9 AND symbol = $10 AND timeframe = $11
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill regime features for existing indicators")
    parser.add_argument("--symbol", default="ETHUSDT", help="Trading pair (default: ETHUSDT)")
    parser.add_argument("--timeframe", default="1h", help="Timeframe (default: 1h)")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size (default: 500)")
    parser.add_argument("--lookback", type=int, default=750, help="Lookback window (default: 750)")
    return parser.parse_args()


async def backfill(symbol: str, timeframe: str, batch_size: int, lookback: int) -> None:
    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 15432)),
        "database": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", "change_me"),
    }

    pool = await asyncpg.create_pool(**db_config)
    try:
        async with pool.acquire() as conn:
            # Get OHLCV data
            rows = await conn.fetch(
                """
                SELECT time, open_price, high_price, low_price, close_price, volume
                FROM ohlcv
                WHERE symbol = $1 AND timeframe = $2
                ORDER BY time ASC
                """,
                symbol,
                timeframe,
            )
            logger.info(f"Found {len(rows)} OHLCV rows for {symbol} {timeframe}")

            if len(rows) < lookback:
                logger.error(f"Not enough data: {len(rows)} < {lookback}")
                return

            # Find rows that need backfill
            null_count = await conn.fetchval(
                """
                SELECT count(*) FROM indicators
                WHERE symbol = $1 AND timeframe = $2
                AND ema_slope_50 IS NULL
                """,
                symbol,
                timeframe,
            )
            logger.info(f"Rows needing backfill: {null_count}")

            if null_count == 0:
                logger.info("Nothing to backfill.")
                return

            total_to_process = len(rows) - lookback
            processed = 0
            batch: list[tuple] = []
            t0 = time.monotonic()

            for i in range(lookback, len(rows)):
                window_start = max(0, i - lookback)
                window = rows[window_start : i + 1]

                data = {
                    "open": [r["open_price"] for r in window],
                    "high": [r["high_price"] for r in window],
                    "low": [r["low_price"] for r in window],
                    "close": [r["close_price"] for r in window],
                    "volume": [r["volume"] for r in window],
                }

                try:
                    indicators = compute_indicators(data)
                except Exception as e:
                    logger.warning(f"Failed at row {i}: {e}")
                    continue

                batch.append(
                    (
                        indicators.ema_slope_50,
                        indicators.volatility_percentile,
                        indicators.atr_percentile,
                        indicators.volume_regime,
                        indicators.price_vs_weekly,
                        indicators.price_vs_monthly,
                        indicators.rsi_slope,
                        indicators.trend_consistency,
                        rows[i]["time"],
                        symbol,
                        timeframe,
                    )
                )

                if len(batch) >= batch_size:
                    await conn.executemany(UPDATE_SQL, batch)
                    processed += len(batch)
                    elapsed = time.monotonic() - t0
                    rate = processed / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"Backfilled {processed}/{total_to_process} "
                        f"({processed * 100 / total_to_process:.1f}%, {rate:.0f} rows/s)"
                    )
                    batch.clear()

            if batch:
                await conn.executemany(UPDATE_SQL, batch)
                processed += len(batch)

            elapsed = time.monotonic() - t0
            logger.info(
                f"Done! Backfilled {processed} rows for {symbol} {timeframe} "
                f"in {elapsed:.1f}s ({processed / elapsed:.0f} rows/s)"
            )

    finally:
        await pool.close()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(backfill(args.symbol, args.timeframe, args.batch_size, args.lookback))
