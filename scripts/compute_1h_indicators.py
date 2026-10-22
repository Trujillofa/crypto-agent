#!/usr/bin/env python3
"""Compute indicators for 1h BTC data including 4h regime indicators."""

import asyncio
import os

import asyncpg
import pandas as pd

from src.db.pool import close_pool, init_pool
from src.features.technical import INDICATOR_WINDOW_BARS, compute_indicators_for_window
from src.features.writer import IndicatorWriter, StoredIndicator
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

    # The shared IndicatorWriter uses the global pool.
    await init_pool(db_config)
    writer = IndicatorWriter(db_config)

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

            for i in range(INDICATOR_WINDOW_BARS - 1, len(rows_1h)):
                # Use the shared live-window definition: exactly
                # INDICATOR_WINDOW_BARS bars ending at (and including) this bar.
                try:
                    indicators = compute_indicators_for_window(
                        rows_1h, index=i, window=INDICATOR_WINDOW_BARS
                    )
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        logger.warning(f"Failed at row {i}: {e}")
                    continue

                row_time = rows_1h[i]["time"]

                # Find matching 4h indicator (most recent before or at this time)
                (
                    df_4h[df_4h["time"] <= row_time].iloc[-1]
                    if len(df_4h[df_4h["time"] <= row_time]) > 0
                    else None
                )

                # Insert into database.
                # The shared IndicatorWriter writes the full column set, so this
                # script can no longer leave `ema_8/10/14/21/24/30`, `sma_40` and
                # `sma_60` NULL while claiming to have written the row.
                try:
                    await writer.write_indicators(
                        StoredIndicator(
                            time=row_time,
                            symbol="BTCUSDT",
                            timeframe="1h",
                            ema_8=indicators.ema_8,
                            ema_10=indicators.ema_10,
                            ema_12=indicators.ema_12,
                            ema_14=indicators.ema_14,
                            ema_21=indicators.ema_21,
                            ema_24=indicators.ema_24,
                            ema_26=indicators.ema_26,
                            ema_30=indicators.ema_30,
                            ema_50=indicators.ema_50,
                            ema_200=indicators.ema_200,
                            sma_20=indicators.sma_20,
                            sma_40=indicators.sma_40,
                            sma_50=indicators.sma_50,
                            sma_60=indicators.sma_60,
                            sma_200=indicators.sma_200,
                            rsi_14=indicators.rsi_14,
                            rsi_7=indicators.rsi_7,
                            macd=indicators.macd,
                            macd_signal=indicators.macd_signal,
                            macd_hist=indicators.macd_hist,
                            bb_upper_dist=indicators.bb_upper_dist,
                            bb_lower_dist=indicators.bb_lower_dist,
                            atr_14=indicators.atr_14,
                            atr_pct=indicators.atr_pct,
                            vwap=indicators.vwap,
                            stoch_k=indicators.stoch_k,
                            stoch_d=indicators.stoch_d,
                            cci=indicators.cci,
                            ema_slope_50=indicators.ema_slope_50,
                            volatility_percentile=indicators.volatility_percentile,
                            atr_percentile=indicators.atr_percentile,
                            volume_regime=indicators.volume_regime,
                            price_vs_weekly=indicators.price_vs_weekly,
                            price_vs_monthly=indicators.price_vs_monthly,
                            rsi_slope=indicators.rsi_slope,
                            trend_consistency=indicators.trend_consistency,
                        )
                    )
                    processed += 1
                except Exception as e:
                    logger.error(f"Database insert failed at row {i}: {e}")
                    continue

                if processed % 1000 == 0:
                    logger.info(
                        f"Processed {processed}/{len(rows_1h) - INDICATOR_WINDOW_BARS} rows "
                        f"(errors: {errors})"
                    )

            logger.info(f"Done! Computed and stored {processed} 1h rows ({errors} errors)")

    finally:
        await close_pool()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(compute_and_store_indicators())
