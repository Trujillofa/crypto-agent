from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pg8000

from src.utils.logger import get_logger


@dataclass(frozen=True)
class StoredIndicator:
    time: str
    symbol: str
    timeframe: str
    rsi_14: float | None
    rsi_7: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    bb_upper_dist: float | None
    bb_lower_dist: float | None
    atr_14: float | None
    atr_pct: float | None
    ema_12: float | None
    ema_26: float | None
    ema_50: float | None
    ema_200: float | None
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    vwap: float | None
    stoch_k: float | None
    stoch_d: float | None
    cci: float | None


class IndicatorWriter:
    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._connected = False
        self._conn: Any | None = None
        self._use_sqlite = False

    async def __aenter__(self) -> "IndicatorWriter":
        await asyncio.to_thread(self._connect)
        await asyncio.to_thread(self._ensure_schema)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
        self._connected = False

    def count_rows(self, table: str) -> int:
        if self._conn is None:
            return 0
        # Validate table name to prevent SQL injection
        valid_tables = {"indicators"}
        if table not in valid_tables:
            raise ValueError(f"Invalid table name: {table}")
        cursor = self._conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cursor.fetchone()[0])

    async def write_indicators(self, indicator: StoredIndicator) -> None:
        if not self._connected:
            raise RuntimeError("IndicatorWriter connection not initialized")
        await asyncio.to_thread(self._insert_row, indicator)

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
            self._logger.info("IndicatorWriter: Connected to TimescaleDB via pg8000")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("IndicatorWriter: Falling back to SQLite: %s", exc)
            sqlite_path = "/tmp/indicators.sqlite"
            self._conn = sqlite3.connect(sqlite_path)
            self._use_sqlite = True
            self._connected = True
            self._logger.info(
                "IndicatorWriter: Connected to SQLite for local persistence"
            )

    def _ensure_schema(self) -> None:
        if not self._connected:
            raise RuntimeError("IndicatorWriter connection not initialized")
        if self._conn is None:
            raise RuntimeError("Database connection missing")
        cursor = self._conn.cursor()

        if self._use_sqlite:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS indicators (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    rsi_14 REAL,
                    rsi_7 REAL,
                    macd REAL,
                    macd_signal REAL,
                    macd_hist REAL,
                    bb_upper_dist REAL,
                    bb_lower_dist REAL,
                    atr_14 REAL,
                    atr_pct REAL,
                    ema_12 REAL,
                    ema_26 REAL,
                    ema_50 REAL,
                    ema_200 REAL,
                    sma_20 REAL,
                    sma_50 REAL,
                    sma_200 REAL,
                    vwap REAL,
                    stoch_k REAL,
                    stoch_d REAL,
                    cci REAL,
                    PRIMARY KEY (time, symbol, timeframe)
                );
                """
            )
        else:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS indicators (
                    time TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    rsi_14 DOUBLE PRECISION,
                    rsi_7 DOUBLE PRECISION,
                    macd DOUBLE PRECISION,
                    macd_signal DOUBLE PRECISION,
                    macd_hist DOUBLE PRECISION,
                    bb_upper_dist DOUBLE PRECISION,
                    bb_lower_dist DOUBLE PRECISION,
                    atr_14 DOUBLE PRECISION,
                    atr_pct DOUBLE PRECISION,
                    ema_12 DOUBLE PRECISION,
                    ema_26 DOUBLE PRECISION,
                    ema_50 DOUBLE PRECISION,
                    ema_200 DOUBLE PRECISION,
                    sma_20 DOUBLE PRECISION,
                    sma_50 DOUBLE PRECISION,
                    sma_200 DOUBLE PRECISION,
                    vwap DOUBLE PRECISION,
                    stoch_k DOUBLE PRECISION,
                    stoch_d DOUBLE PRECISION,
                    cci DOUBLE PRECISION,
                    PRIMARY KEY (time, symbol, timeframe)
                );
                """
            )
            cursor.execute(
                """
                SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);
                """
            )
        self._conn.commit()

    def _insert_row(self, indicator: StoredIndicator) -> None:
        if not self._connected:
            raise RuntimeError("IndicatorWriter connection not initialized")
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        values = (
            indicator.time,
            indicator.symbol,
            indicator.timeframe,
            indicator.rsi_14,
            indicator.rsi_7,
            indicator.macd,
            indicator.macd_signal,
            indicator.macd_hist,
            indicator.bb_upper_dist,
            indicator.bb_lower_dist,
            indicator.atr_14,
            indicator.atr_pct,
            indicator.ema_12,
            indicator.ema_26,
            indicator.ema_50,
            indicator.ema_200,
            indicator.sma_20,
            indicator.sma_50,
            indicator.sma_200,
            indicator.vwap,
            indicator.stoch_k,
            indicator.stoch_d,
            indicator.cci,
        )

        try:
            self._do_insert(values)
        except Exception:
            if not self._use_sqlite:
                self._conn.rollback()
            raise

    def _do_insert(self, values: tuple[object, ...]) -> None:
        if self._conn is None:
            raise RuntimeError("Database connection missing")
        cursor = self._conn.cursor()

        if self._use_sqlite:
            cursor.execute(
                """
                INSERT INTO indicators (
                    time, symbol, timeframe,
                    rsi_14, rsi_7, macd, macd_signal, macd_hist,
                    bb_upper_dist, bb_lower_dist, atr_14, atr_pct,
                    ema_12, ema_26, ema_50, ema_200,
                    sma_20, sma_50, sma_200,
                    vwap, stoch_k, stoch_d, cci
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(time, symbol, timeframe) DO UPDATE SET
                    rsi_14=excluded.rsi_14,
                    rsi_7=excluded.rsi_7,
                    macd=excluded.macd,
                    macd_signal=excluded.macd_signal,
                    macd_hist=excluded.macd_hist,
                    bb_upper_dist=excluded.bb_upper_dist,
                    bb_lower_dist=excluded.bb_lower_dist,
                    atr_14=excluded.atr_14,
                    atr_pct=excluded.atr_pct,
                    ema_12=excluded.ema_12,
                    ema_26=excluded.ema_26,
                    ema_50=excluded.ema_50,
                    ema_200=excluded.ema_200,
                    sma_20=excluded.sma_20,
                    sma_50=excluded.sma_50,
                    sma_200=excluded.sma_200,
                    vwap=excluded.vwap,
                    stoch_k=excluded.stoch_k,
                    stoch_d=excluded.stoch_d,
                    cci=excluded.cci;
                """,
                values,
            )
        else:
            cursor.execute(
                """
                INSERT INTO indicators (
                    time, symbol, timeframe,
                    rsi_14, rsi_7, macd, macd_signal, macd_hist,
                    bb_upper_dist, bb_lower_dist, atr_14, atr_pct,
                    ema_12, ema_26, ema_50, ema_200,
                    sma_20, sma_50, sma_200,
                    vwap, stoch_k, stoch_d, cci
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                    rsi_14 = EXCLUDED.rsi_14,
                    rsi_7 = EXCLUDED.rsi_7,
                    macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal,
                    macd_hist = EXCLUDED.macd_hist,
                    bb_upper_dist = EXCLUDED.bb_upper_dist,
                    bb_lower_dist = EXCLUDED.bb_lower_dist,
                    atr_14 = EXCLUDED.atr_14,
                    atr_pct = EXCLUDED.atr_pct,
                    ema_12 = EXCLUDED.ema_12,
                    ema_26 = EXCLUDED.ema_26,
                    ema_50 = EXCLUDED.ema_50,
                    ema_200 = EXCLUDED.ema_200,
                    sma_20 = EXCLUDED.sma_20,
                    sma_50 = EXCLUDED.sma_50,
                    sma_200 = EXCLUDED.sma_200,
                    vwap = EXCLUDED.vwap,
                    stoch_k = EXCLUDED.stoch_k,
                    stoch_d = EXCLUDED.stoch_d,
                    cci = EXCLUDED.cci;
                """,
                values,
            )
        self._conn.commit()
