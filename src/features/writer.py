from __future__ import annotations

from collections.abc import Mapping
import asyncio
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from src.utils.logger import get_logger


@dataclass(frozen=True)
class StoredIndicator:
    time: datetime
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
        self._conn: asyncpg.Connection | None = None
        self._db_lock = asyncio.Lock()

    async def __aenter__(self) -> "IndicatorWriter":
        await self._connect()
        await self._ensure_schema()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await self._conn.close()
        self._connected = False

    async def count_rows(self, table: str) -> int:
        async with self._db_lock:
            if self._conn is None:
                return 0
        # Validate table name to prevent SQL injection
        valid_tables = {"indicators"}
        if table not in valid_tables:
            raise ValueError(f"Invalid table name: {table}")
        query = f"SELECT COUNT(*) FROM {table}"
        count = await self._conn.fetchval(query)
        return int(count or 0)

    async def write_indicators(self, indicator: StoredIndicator) -> None:
        async with self._db_lock:
            if not self._connected:
                raise RuntimeError("IndicatorWriter connection not initialized")
            await self._insert_row(indicator)

    async def _connect(self) -> None:
        host = str(self._config.get("host", "localhost"))
        port = int(self._config.get("port", 5432))
        database = str(self._config.get("name", "marketdata"))
        user = str(self._config.get("user", "trading"))
        password = str(self._config.get("password", ""))

        self._conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        self._connected = True
        self._logger.info("IndicatorWriter: Connected to TimescaleDB via asyncpg")

    async def _ensure_schema(self) -> None:
        if not self._connected:
            raise RuntimeError("IndicatorWriter connection not initialized")
        if self._conn is None:
            raise RuntimeError("Database connection missing")
        await self._conn.execute("""
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
            """)
        await self._conn.execute("""
            SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);
            """)

    async def _insert_row(self, indicator: StoredIndicator) -> None:
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

        await self._conn.execute(
            """
            INSERT INTO indicators (
                time, symbol, timeframe,
                rsi_14, rsi_7, macd, macd_signal, macd_hist,
                bb_upper_dist, bb_lower_dist, atr_14, atr_pct,
                ema_12, ema_26, ema_50, ema_200,
                sma_20, sma_50, sma_200,
                vwap, stoch_k, stoch_d, cci
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                $14, $15, $16, $17, $18, $19, $20, $21, $22, $23
            )
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
            *values,
        )
