#!/usr/bin/env python3
"""Compute indicators for downloaded historical data."""

import asyncio
import os

import asyncpg
import pandas as pd
from src.features.technical import compute_indicators
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def compute_and_store_indicators():
    """Compute indicators for all OHLCV data and store in indicators table."""
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

    try:
        async with pool.acquire() as conn:
            # Get all OHLCV data for BTCUSDT 4h
            rows = await conn.fetch(
                """
                SELECT time, open_price, high_price, low_price, close_price, volume
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = '4h'
                ORDER BY time ASC
                """
            )

            logger.info(f"Found {len(rows)} OHLCV rows to process")

            # Need at least 750 periods for all regime features
            # (monthly VWAP needs 30*24 periods which assumes 1h data - but we have 4h)
            min_required = 750

            if len(rows) < min_required:
                logger.error(f"Not enough data. Have {len(rows)}, need at least {min_required}")
                return

            # Process starting from row with enough lookback
            start_idx = min_required
            processed = 0

            for i in range(start_idx, len(rows)):
                # Get window of data up to current row
                window_start = max(0, i - min_required)
                window = rows[window_start : i + 1]

                data = {
                    "open": [r["open_price"] for r in window],
                    "high": [r["high_price"] for r in window],
                    "low": [r["low_price"] for r in window],
                    "close": [r["close_price"] for r in window],
                    "volume": [r["volume"] for r in window],
                }

                try:
                    indicators = compute_indicators(data)
                except Exception as e:
                    logger.warning(f"Failed to compute indicators for row {i}: {e}")
                    continue

                row_time = rows[i]["time"]

                # Insert into database
                await conn.execute(
                    """
                    INSERT INTO indicators (
                        time, symbol, timeframe,
                        ema_12, ema_26, ema_50, ema_200,
                        sma_20, sma_50, sma_200,
                        rsi_14, rsi_7,
                        macd, macd_signal, macd_hist,
                        bb_upper_dist, bb_lower_dist,
                        atr_14, atr_pct,
                        vwap,
                        stoch_k, stoch_d, cci,
                        ema_slope_50, volatility_percentile, atr_percentile,
                        volume_regime, price_vs_weekly, price_vs_monthly,
                        rsi_slope, trend_consistency
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31)
                    ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                        ema_12 = EXCLUDED.ema_12,
                        ema_26 = EXCLUDED.ema_26,
                        ema_50 = EXCLUDED.ema_50,
                        ema_200 = EXCLUDED.ema_200,
                        sma_20 = EXCLUDED.sma_20,
                        sma_50 = EXCLUDED.sma_50,
                        sma_200 = EXCLUDED.sma_200,
                        rsi_14 = EXCLUDED.rsi_14,
                        rsi_7 = EXCLUDED.rsi_7,
                        macd = EXCLUDED.macd,
                        macd_signal = EXCLUDED.macd_signal,
                        macd_hist = EXCLUDED.macd_hist,
                        bb_upper_dist = EXCLUDED.bb_upper_dist,
                        bb_lower_dist = EXCLUDED.bb_lower_dist,
                        atr_14 = EXCLUDED.atr_14,
                        atr_pct = EXCLUDED.atr_pct,
                        vwap = EXCLUDED.vwap,
                        stoch_k = EXCLUDED.stoch_k,
                        stoch_d = EXCLUDED.stoch_d,
                        cci = EXCLUDED.cci,
                        ema_slope_50 = EXCLUDED.ema_slope_50,
                        volatility_percentile = EXCLUDED.volatility_percentile,
                        atr_percentile = EXCLUDED.atr_percentile,
                        volume_regime = EXCLUDED.volume_regime,
                        price_vs_weekly = EXCLUDED.price_vs_weekly,
                        price_vs_monthly = EXCLUDED.price_vs_monthly,
                        rsi_slope = EXCLUDED.rsi_slope,
                        trend_consistency = EXCLUDED.trend_consistency
                    """,
                    row_time,
                    "BTCUSDT",
                    "4h",
                    indicators.ema_12,
                    indicators.ema_26,
                    indicators.ema_50,
                    indicators.ema_200,
                    indicators.sma_20,
                    indicators.sma_50,
                    indicators.sma_200,
                    indicators.rsi_14,
                    indicators.rsi_7,
                    indicators.macd,
                    indicators.macd_signal,
                    indicators.macd_hist,
                    indicators.bb_upper_dist,
                    indicators.bb_lower_dist,
                    indicators.atr_14,
                    indicators.atr_pct,
                    indicators.vwap,
                    indicators.stoch_k,
                    indicators.stoch_d,
                    indicators.cci,
                    indicators.ema_slope_50,
                    indicators.volatility_percentile,
                    indicators.atr_percentile,
                    indicators.volume_regime,
                    indicators.price_vs_weekly,
                    indicators.price_vs_monthly,
                    indicators.rsi_slope,
                    indicators.trend_consistency,
                )

                processed += 1
                if processed % 100 == 0:
                    logger.info(f"Processed {processed}/{len(rows) - start_idx} rows")

            logger.info(f"Done! Computed indicators for {processed} rows")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(compute_and_store_indicators())
