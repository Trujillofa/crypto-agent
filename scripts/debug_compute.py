#!/usr/bin/env python3
"""Debug indicator computation - test specific row."""

import os
import pandas as pd
import asyncpg
import asyncio


async def debug():
    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 15432)),
        "database": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", "change_me"),
    }

    pool = await asyncpg.create_pool(
        host=db_config["host"],
        port=db_config["port"],
        database=db_config["database"],
        user=db_config["user"],
        password=db_config["password"],
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT time, open_price, high_price, low_price, close_price, volume
            FROM ohlcv
            WHERE symbol = 'BTCUSDT' AND timeframe = '4h'
            ORDER BY time ASC
            """
        )

        print(f"Total rows: {len(rows)}")

        # Test row 250
        i = 250
        window = rows[: i + 1]

        data = {
            "open": [r["open_price"] for r in window],
            "high": [r["high_price"] for r in window],
            "low": [r["low_price"] for r in window],
            "close": [r["close_price"] for r in window],
            "volume": [r["volume"] for r in window],
        }

        print(f"\nRow {i}:")
        print(f"  Data points: {len(data['close'])}")
        print(f"  First close: {data['close'][0]}")
        print(f"  Last close: {data['close'][-1]}")

        # Try compute_indicators
        from src.features.technical import compute_indicators

        try:
            indicators = compute_indicators(data)
            print(f"  SUCCESS!")
            print(f"  RSI: {indicators.rsi_14}")
            print(f"  Trend consistency: {indicators.trend_consistency}")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback

            traceback.print_exc()

    await pool.close()


if __name__ == "__main__":
    asyncio.run(debug())
