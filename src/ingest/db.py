from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

from src.db.pool import get_pool, is_connected
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.utils.logger import get_logger


class TimescaleWriter:
    def __init__(self, config: Mapping[str, object], metrics: IngestMetrics) -> None:
        self._config = config
        self._metrics = metrics
        self._logger = get_logger(self.__class__.__name__)
        self._db_lock = asyncio.Lock()

    async def __aenter__(self) -> TimescaleWriter:
        await self._ensure_schema()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        pass

    def is_connected(self) -> bool:
        """Check if the database is connected (thread-safe)."""
        return is_connected()

    async def count_rows(self, table: str) -> int:
        async with self._db_lock:
            # Validate table name to prevent SQL injection
            valid_tables = {"ohlcv"}
            if table not in valid_tables:
                raise ValueError(f"Invalid table name: {table}")
            query = f"SELECT COUNT(*) FROM {table}"
            pool = get_pool()
            async with pool.acquire() as conn:
                count = await conn.fetchval(query)
                return int(count or 0)

    async def write_ohlcv(self, candle: Ohlcv) -> None:
        async with self._db_lock:
            start_time = time.perf_counter()
            await self._insert_row(candle)
        elapsed = time.perf_counter() - start_time
        self._metrics.insert_latency_seconds.set(elapsed)

    async def _ensure_schema(self) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    time TIMESTAMPTZ NOT NULL,
                    close_time TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_price DOUBLE PRECISION NOT NULL,
                    high_price DOUBLE PRECISION NOT NULL,
                    low_price DOUBLE PRECISION NOT NULL,
                    close_price DOUBLE PRECISION NOT NULL,
                    volume DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (time, symbol, timeframe)
                );
                """)
            await conn.execute("""
                SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);
                """)

    async def _insert_row(self, candle: Ohlcv) -> None:
        values = (
            candle.open_time_utc,
            candle.close_time_utc,
            candle.symbol,
            candle.timeframe,
            candle.open_price,
            candle.high_price,
            candle.low_price,
            candle.close_price,
            candle.volume,
        )
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ohlcv (
                    time, close_time, symbol, timeframe,
                    open_price, high_price, low_price, close_price, volume
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                    close_time = EXCLUDED.close_time,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume;
                """,
                *values,
            )
