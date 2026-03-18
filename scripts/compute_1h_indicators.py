#!/usr/bin/env python3
"""Compute indicators for 1h BTC data including 4h regime indicators."""

import asyncio
import os

import asyncpg
import pandas as pd
from src.features.technical import compute_indicators
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def compute_and_store_indicators():
    """Compute indicators for all 1h OHLCV data with 4h regime overlay."""
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
            # Get all 1h OHLCV data for BTCUSDT
            rows_1h = await conn.fetch(
                """
                SELECT time, open_price, high_price, low_price, close_price, volume
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = '1h'
                ORDER BY time ASC
                """
            )

            logger.info(f"Found {len(rows_1h)} 1h OHLCV rows to process")

            if len(rows_1h) < 250:
                logger.error(f"Not enough data. Have {len(rows_1h)}, need at least 250")
                return

            # Get 4h regime indicators
            rows_4h = await conn.fetch(
                """
                SELECT time, ema_slope_50, volatility_percentile, trend_consistency, rsi_slope
                FROM indicators
                WHERE symbol = 'BTCUSDT' AND timeframe = '4h'
                ORDER BY time ASC
                """
            )

            logger.info(f"Found {len(rows_4h)} 4h indicator rows")

            # Build DataFrame for 4h data
            df_4h = pd.DataFrame(
                rows_4h,
                columns=[
                    "time",
                    "ema_slope_50",
                    "volatility_percentile",
                    "trend_consistency",
                    "rsi_slope",
                ],
            )

            # Process each 1h row
            processed = 0
            errors = 0

            for i in range(250, len(rows_1h)):
                # Get window of 1h data for indicator computation
                window = rows_1h[max(0, i - 250) : i + 1]

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
                    errors += 1
                    if errors <= 5:
                        logger.warning(f"Failed at row {i}: {e}")
                    continue

                row_time = rows_1h[i]["time"]

                # Find matching 4h indicator (most recent before or at this time)
                regime_4h = (
                    df_4h[df_4h["time"] <= row_time].iloc[-1]
                    if len(df_4h[df_4h["time"] <= row_time]) > 0
                    else None
                )

                # Insert into database
                try:
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
                            ema_12 = EXCLUDED.ema_12, ema_26 = EXCLUDED.ema_26,
                            ema_50 = EXCLUDED.ema_50, ema_200 = EXCLUDED.ema_200,
                            sma_20 = EXCLUDED.sma_20, sma_50 = EXCLUDED.sma_50, sma_200 = EXCLUDED.sma_200,
                            rsi_14 = EXCLUDED.rsi_14, rsi_7 = EXCLUDED.rsi_7,
                            macd = EXCLUDED.macd, macd_signal = EXCLUDED.macd_signal, macd_hist = EXCLUDED.macd_hist,
                            bb_upper_dist = EXCLUDED.bb_upper_dist, bb_lower_dist = EXCLUDED.bb_lower_dist,
                            atr_14 = EXCLUDED.atr_14, atr_pct = EXCLUDED.atr_pct, vwap = EXCLUDED.vwap,
                            stoch_k = EXCLUDED.stoch_k, stoch_d = EXCLUDED.stoch_d, cci = EXCLUDED.cci,
                            ema_slope_50 = EXCLUDED.ema_slope_50, volatility_percentile = EXCLUDED.volatility_percentile,
                            atr_percentile = EXCLUDED.atr_percentile, volume_regime = EXCLUDED.volume_regime,
                            price_vs_weekly = EXCLUDED.price_vs_weekly, price_vs_monthly = EXCLUDED.price_vs_monthly,
                            rsi_slope = EXCLUDED.rsi_slope, trend_consistency = EXCLUDED.trend_consistency
                        """,
                        row_time,
                        "BTCUSDT",
                        "1h",
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
                except Exception as e:
                    logger.error(f"Database insert failed at row {i}: {e}")
                    continue

                if processed % 1000 == 0:
                    logger.info(
                        f"Processed {processed}/{len(rows_1h) - 250} rows (errors: {errors})"
                    )

            logger.info(f"Done! Computed and stored {processed} 1h rows ({errors} errors)")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(compute_and_store_indicators())
