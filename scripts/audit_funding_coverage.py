#!/usr/bin/env python3
"""Audit funding_rates table coverage for Phase 3 planning."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import close_pool, get_pool, init_pool  # noqa: E402


async def main(symbols: list[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for symbol in symbols:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS n,
                       MIN(funding_time) AS min_time,
                       MAX(funding_time) AS max_time
                FROM funding_rates
                WHERE symbol = $1
                """,
                symbol,
            )
            print(f"{symbol}: rows={row['n']} range={row['min_time']} .. {row['max_time']}")
    await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,AVAXUSDT",
        help="Comma-separated symbols",
    )
    args = parser.parse_args()
    init_pool(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    asyncio.run(main([s.strip() for s in args.symbols.split(",") if s.strip()]))
