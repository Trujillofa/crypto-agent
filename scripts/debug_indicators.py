#!/usr/bin/env python3
"""Debug indicator computation to identify which one is failing."""

import os
import pandas as pd
from src.features.technical import compute_indicators, _atr, _atr_percentile
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Load the data
db_config = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 15432)),
    "database": os.getenv("DB_NAME", "marketdata"),
    "user": os.getenv("DB_USER", "trading"),
    "password": os.getenv("DB_PASSWORD", "change_me"),
}

import asyncpg
import asyncio


async def debug():
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

        # Test with row 250 (first one that fails)
        i = 250
        window = rows[: i + 1]

        data = {
            "open": [r["open_price"] for r in window],
            "high": [r["high_price"] for r in window],
            "low": [r["low_price"] for r in window],
            "close": [r["close_price"] for r in window],
            "volume": [r["volume"] for r in window],
        }

        print(f"Data window size: {len(data['close'])}")

        high = data["high"]
        low = data["low"]
        close = data["close"]

        print(f"\nDebug _atr_percentile:")
        print(f"len(close) = {len(close)}")
        print(f"Need: lookback(20) + atr_period(14) + 1 = 35")

        atr_period = 14
        lookback = 20

        # Check the logic
        print(f"\nLoop: for i in range({atr_period}, {len(close)})")
        print(f"First i = {atr_period}")

        i = atr_period
        h_window = high[i - atr_period : i + 1]
        l_window = low[i - atr_period : i + 1]
        c_window = close[i - atr_period : i + 1]

        print(f"\nAt i={i}:")
        print(
            f"  h_window = high[{i - atr_period}:{i + 1}] = high[{i - atr_period}:{i + 1}] -> size {len(h_window)}"
        )
        print(f"  l_window = low[{i - atr_period}:{i + 1}] -> size {len(l_window)}")
        print(f"  c_window = close[{i - atr_period}:{i + 1}] -> size {len(c_window)}")

        print(f"\nIn _atr function:")
        print(f"  for index in range(1, len(c_window)):")
        print(f"  len(c_window) = {len(c_window)}")
        print(f"  So index goes from 1 to {len(c_window) - 1}")
        print(f"  h_window has {len(h_window)} elements (indices 0 to {len(h_window) - 1})")
        print(f"  When index={len(c_window) - 1}, h_window[{len(c_window) - 1}] will fail!")

        try:
            atr = _atr(h_window, l_window, c_window, atr_period)
            print(f"\nATR computed successfully: {atr}")
        except Exception as e:
            print(f"\nError computing ATR: {e}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(debug())
