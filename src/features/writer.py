from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from src.db.pool import get_pool
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
    # Regime Features (NEW)
    ema_slope_50: float | None
    volatility_percentile: float | None
    atr_percentile: float | None
    volume_regime: float | None
    price_vs_weekly: float | None
    price_vs_monthly: float | None
    rsi_slope: float | None
    trend_consistency: float | None


class IndicatorWriter:
    """Writes computed indicators to TimescaleDB.

    Uses a shared connection pool for efficient database access.
    """

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._db_lock = asyncio.Lock()

    async def __aenter__(self) -> IndicatorWriter:
        await self._ensure_schema()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Pool is managed globally
        pass

    async def count_rows(self, table: str) -> int:
        """Count total rows in a table."""
        # Validate table name to prevent SQL injection
        valid_tables = {"indicators"}
        if table not in valid_tables:
            raise ValueError(f"Invalid table name: {table}")

        query = f"SELECT COUNT(*) FROM {table}"
        pool = get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(query)
            return int(count or 0)

    async def write_indicators(self, indicator: StoredIndicator) -> None:
        """Write indicator values to database."""
        async with self._db_lock:
            await self._insert_row(indicator)

    async def _ensure_schema(self) -> None:
        """Ensure the indicators table and hypertable exist."""
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
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
                    -- Regime Features (NEW)
                    ema_slope_50 DOUBLE PRECISION,
                    volatility_percentile DOUBLE PRECISION,
                    atr_percentile DOUBLE PRECISION,
                    volume_regime DOUBLE PRECISION,
                    price_vs_weekly DOUBLE PRECISION,
                    price_vs_monthly DOUBLE PRECISION,
                    rsi_slope DOUBLE PRECISION,
                    trend_consistency DOUBLE PRECISION,

                    PRIMARY KEY (time, symbol, timeframe)
                );
                """)
            await conn.execute("""
                SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);
                """)

    async def _insert_row(self, indicator: StoredIndicator) -> None:
        """Insert a single indicator row into the database."""
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
            # Regime Features (NEW)
            indicator.ema_slope_50,
            indicator.volatility_percentile,
            indicator.atr_percentile,
            indicator.volume_regime,
            indicator.price_vs_weekly,
            indicator.price_vs_monthly,
            indicator.rsi_slope,
            indicator.trend_consistency,
        )

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO indicators (
                    time, symbol, timeframe,
                    rsi_14, rsi_7, macd, macd_signal, macd_hist,
                    bb_upper_dist, bb_lower_dist, atr_14, atr_pct,
                    ema_12, ema_26, ema_50, ema_200,
                    sma_20, sma_50, sma_200,
                    vwap, stoch_k, stoch_d, cci,
                    -- Regime Features (NEW)
                    ema_slope_50, volatility_percentile, atr_percentile,
                    volume_regime, price_vs_weekly, price_vs_monthly,
                    rsi_slope, trend_consistency
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17, $18, $19, $20, $21, $22, $23,
                    $24, $25, $26, $27, $28, $29, $30, $31
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
                    cci = EXCLUDED.cci,
                    -- Regime Features (NEW)
                    ema_slope_50 = EXCLUDED.ema_slope_50,
                    volatility_percentile = EXCLUDED.volatility_percentile,
                    atr_percentile = EXCLUDED.atr_percentile,
                    volume_regime = EXCLUDED.volume_regime,
                    price_vs_weekly = EXCLUDED.price_vs_weekly,
                    price_vs_monthly = EXCLUDED.price_vs_monthly,
                    rsi_slope = EXCLUDED.rsi_slope,
                    trend_consistency = EXCLUDED.trend_consistency;
                """,
                *values,
            )
