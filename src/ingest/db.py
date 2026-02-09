from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pg8000

from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.utils.logger import get_logger


class TimescaleWriter:
    def __init__(self, config: Mapping[str, object], metrics: IngestMetrics) -> None:
        self._config = config
        self._metrics = metrics
        self._logger = get_logger(self.__class__.__name__)
        self._connected = False
        self._conn: Any | None = None
        self._use_sqlite = False

    async def __aenter__(self) -> "TimescaleWriter":
        await asyncio.to_thread(self._connect)
        await asyncio.to_thread(self._ensure_schema)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def count_rows(self, table: str) -> int:
        if self._conn is None:
            return 0
        # Validate table name to prevent SQL injection
        valid_tables = {"ohlcv"}
        if table not in valid_tables:
            raise ValueError(f"Invalid table name: {table}")
        cursor = self._conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cursor.fetchone()[0])

    async def write_ohlcv(self, candle: Ohlcv) -> None:
        start_time = time.perf_counter()
        await asyncio.to_thread(self._insert_row, candle)
        elapsed = time.perf_counter() - start_time
        self._metrics.insert_latency_seconds.set(elapsed)

    def _connect(self) -> None:
        host = str(self._config.get("host", "localhost"))
        port = int(self._config.get("port", 5432))
        database = str(self._config.get("name", "marketdata"))
        user = str(self._config.get("user", "trading"))
        password = str(self._config.get("password", ""))

        try:
            self._conn = pg8000.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
            )
            self._use_sqlite = False
            self._connected = True
            self._logger.info("Connected to TimescaleDB via pg8000")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Falling back to SQLite: %s", exc)
            sqlite_path = Path("data/ohlcv.sqlite")
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(sqlite_path)
            self._use_sqlite = True
            self._connected = True
            self._logger.info("Connected to SQLite for local persistence")

    def _ensure_schema(self) -> None:
        if not self._connected:
            raise RuntimeError("TimescaleDB connection not initialized")
        if self._conn is None:
            raise RuntimeError("Database connection missing")
        cursor = self._conn.cursor()
        if self._use_sqlite:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv (
                    time TEXT NOT NULL,
                    close_time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_price REAL NOT NULL,
                    high_price REAL NOT NULL,
                    low_price REAL NOT NULL,
                    close_price REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (time, symbol, timeframe)
                );
                """
            )
        else:
            cursor.execute(
                """
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
                """
            )
            cursor.execute(
                """
                SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);
                """
            )
        self._conn.commit()

    def _insert_row(self, candle: Ohlcv) -> None:
        if not self._connected:
            raise RuntimeError("TimescaleDB connection not initialized")
        if self._conn is None:
            raise RuntimeError("Database connection missing")
        cursor = self._conn.cursor()
        values = (
            candle.open_time_utc.isoformat(),
            candle.close_time_utc.isoformat(),
            candle.symbol,
            candle.timeframe,
            candle.open_price,
            candle.high_price,
            candle.low_price,
            candle.close_price,
            candle.volume,
        )
        if self._use_sqlite:
            cursor.execute(
                """
                INSERT INTO ohlcv (
                    time, close_time, symbol, timeframe,
                    open_price, high_price, low_price, close_price, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(time, symbol, timeframe) DO UPDATE SET
                    close_time=excluded.close_time,
                    open_price=excluded.open_price,
                    high_price=excluded.high_price,
                    low_price=excluded.low_price,
                    close_price=excluded.close_price,
                    volume=excluded.volume;
                """,
                values,
            )
        else:
            cursor.execute(
                """
                INSERT INTO ohlcv (
                    time, close_time, symbol, timeframe,
                    open_price, high_price, low_price, close_price, volume
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                    close_time = EXCLUDED.close_time,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume;
                """,
                values,
            )
        self._conn.commit()
