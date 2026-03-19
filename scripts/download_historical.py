#!/usr/bin/env python3
"""Bulk historical data downloader for backtesting."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aiohttp

from src.db.pool import close_pool, init_pool
from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.utils.logger import get_logger


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--db", action="store_true")
    return parser.parse_args(argv)


class HistoricalKlineClient(Protocol):
    async def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int,
        limit: int,
    ) -> list[list[object]]: ...


class BinanceHistoricalClient:
    def __init__(self) -> None:
        self._base_url = "https://api.binance.com"
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> BinanceHistoricalClient:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int,
        limit: int,
    ) -> list[list[object]]:
        if self._session is None:
            raise RuntimeError("BinanceHistoricalClient session is not initialized")

        async with self._session.get(
            f"{self._base_url}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_time,
                "limit": limit,
            },
        ) as response:
            response.raise_for_status()
            payload = await response.json()
            if not isinstance(payload, list):
                raise ValueError(f"Unexpected Binance response: {payload}")
            return payload


def _normalize_kline(symbol: str, interval: str, kline: Sequence[object]) -> dict[str, object]:
    return {
        "time": datetime.fromtimestamp(int(kline[0]) / 1000, tz=UTC),
        "symbol": symbol,
        "timeframe": interval,
        "open_price": float(kline[1]),
        "high_price": float(kline[2]),
        "low_price": float(kline[3]),
        "close_price": float(kline[4]),
        "volume": float(kline[5]),
        "close_time": int(kline[6]),
        "quote_volume": float(kline[7]),
        "trades": int(kline[8]),
        "taker_buy_base": float(kline[9]),
        "taker_buy_quote": float(kline[10]),
    }


async def download_klines(
    client: HistoricalKlineClient,
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> list[dict[str, object]]:
    logger = get_logger(__name__)
    data: list[dict[str, object]] = []
    start_ts = int(datetime.fromisoformat(start).timestamp() * 1000)
    end_ts = int(datetime.fromisoformat(end).timestamp() * 1000)

    while start_ts < end_ts:
        klines = await client._fetch_klines(
            symbol,
            interval,
            start_time=start_ts,
            limit=1000,
        )
        if not klines:
            break
        data.extend(_normalize_kline(symbol, interval, kline) for kline in klines)
        start_ts = int(klines[-1][0]) + 1
        logger.info("Downloaded %s candles for %s %s", len(klines), symbol, interval)
        await asyncio.sleep(0.1)

    return data


def save_to_csv(rows: Sequence[dict[str, object]], csv_path: Path) -> None:
    fieldnames = [
        "time",
        "symbol",
        "timeframe",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            serialized["time"] = row["time"].isoformat()
            writer.writerow(serialized)


async def load_to_db(writer: TimescaleWriter, rows: Sequence[dict[str, object]]) -> None:
    for row in rows:
        candle = Ohlcv(
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            open_time=row["time"],
            close_time=datetime.fromtimestamp(int(row["close_time"]) / 1000, tz=UTC),
            open_price=float(row["open_price"]),
            high_price=float(row["high_price"]),
            low_price=float(row["low_price"]),
            close_price=float(row["close_price"]),
            volume=float(row["volume"]),
        )
        await writer.write_ohlcv(candle)


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    logger = get_logger(__name__)

    async with BinanceHistoricalClient() as client:
        rows = await download_klines(client, args.symbol, args.interval, args.start, args.end)
    logger.info("Downloaded %s candles", len(rows))

    Path("data").mkdir(exist_ok=True)
    csv_path = Path(f"data/{args.symbol}_{args.interval}_{args.start}_{args.end}.csv")
    save_to_csv(rows, csv_path)
    logger.info("Saved %s", csv_path)

    if args.db:
        db_config = {
            "host": os.getenv("DB_HOST", "timescaledb"),
            "port": int(os.getenv("DB_PORT", 5432)),
            "name": os.getenv("DB_NAME", "marketdata"),
            "user": os.getenv("DB_USER", "trading"),
            "password": os.getenv("DB_PASSWORD", ""),
        }
        await init_pool(db_config)
        writer = TimescaleWriter(db_config, IngestMetrics())
        await writer.__aenter__()
        try:
            await load_to_db(writer, rows)
        finally:
            await writer.__aexit__(None, None, None)
            await close_pool()
        logger.info("Loaded to DB")


if __name__ == "__main__":
    asyncio.run(main())
