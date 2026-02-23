#!/usr/bin/env python3
"""Bulk historical data downloader for backtesting."""

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from src.ingest.binance import BinanceClient
from src.ingest.db import OhlcvWriter
from src.utils.logger import get_logger


async def download_klines(client, symbol, interval, start, end):
    logger = get_logger(__name__)
    data = []
    start_ts = int(pd.to_datetime(start).timestamp() * 1000)
    end_ts = int(pd.to_datetime(end).timestamp() * 1000)

    while start_ts < end_ts:
        klines = await client._fetch_klines(
            symbol, interval, start_time=start_ts, limit=1000
        )
        if not klines:
            break
        data.extend(klines)
        start_ts = klines[-1][0] + 1
        logger.info(f"Downloaded {len(klines)} for {symbol}")
        await asyncio.sleep(0.1)

    df = pd.DataFrame(
        data,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


async def load_to_db(writer, df):
    for _, row in df.iterrows():
        candle = {
            "time": row["open_time"],
            "symbol": writer.symbol,
            "timeframe": writer.timeframe,
            "open_price": float(row["open"]),
            "high_price": float(row["high"]),
            "low_price": float(row["low"]),
            "close_price": float(row["close"]),
            "volume": float(row["volume"]),
        }
        await writer.write_ohlcv(candle)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--db", action="store_true")
    args = parser.parse_args()

    logger = get_logger(__name__)

    client = BinanceClient()
    df = await download_klines(client, args.symbol, args.interval, args.start, args.end)
    logger.info(f"Downloaded {len(df)} candles")

    Path("data").mkdir(exist_ok=True)
    csv_path = f"data/{args.symbol}_{args.interval}_{args.start}_{args.end}.csv"
    df.to_csv(csv_path, index=False)
    logger.info(f"Saved {csv_path}")

    if args.db:
        db_config = {
            "host": os.getenv("DB_HOST", "timescaledb"),
            "port": int(os.getenv("DB_PORT", 5432)),
            "name": os.getenv("DB_NAME", "marketdata"),
            "user": os.getenv("DB_USER", "trading"),
            "password": os.getenv("DB_PASSWORD", ""),
        }
        writer = OhlcvWriter(args.symbol, args.interval, db_config)
        await writer.__aenter__()
        await load_to_db(writer, df)
        await writer.__aexit__(None, None, None)
        logger.info("Loaded to DB")


if __name__ == "__main__":
    asyncio.run(main())
