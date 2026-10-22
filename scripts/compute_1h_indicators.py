#!/usr/bin/env python3
"""Compute indicators for 1h BTC data including 4h regime indicators."""

import argparse
import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reuse the shared config builder so every backfill resolves the same
# database, with the same key names the shared pool expects.
from scripts.compute_basic_indicators import build_db_config  # noqa: E402
from src.db.pool import close_pool, init_pool
from src.features.technical import (
    INDICATOR_WINDOW_BARS,
    compute_indicators_for_window,
    newest_closed_index,
)
from src.features.writer import IndicatorWriter, StoredIndicator
from src.utils.logger import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute indicators for 1h BTC data and store them in the indicators table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without touching the database",
    )
    return parser.parse_args(argv)


async def compute_and_store_indicators(dry_run: bool = False) -> None:
    """Compute indicators for all 1h OHLCV data with 4h regime overlay."""
    db_config = build_db_config()

    # The shared IndicatorWriter uses the global pool, so this pool is also
    # the one the writer draws connections from. One pool, one config.
    pool = await init_pool(db_config)
    writer = IndicatorWriter(db_config)
    await writer._ensure_schema()

    if dry_run:
        logger.warning("DRY RUN: no rows will be written to the indicators table.")

    try:
        async with pool.acquire() as conn:
            # Get all 1h OHLCV data for BTCUSDT
            rows_1h = await conn.fetch(
                """
                SELECT time, close_time, open_price, high_price, low_price, close_price, volume
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = '1h'
                ORDER BY time ASC
                """
            )

            # The newest 1h row may still be a FORMING candle (the REST
            # poller writes Binance's in-progress kline). The live path
            # publishes only the newest CLOSED candle, so this backfill
            # must not write a row for the forming bar either -- or the two
            # paths would disagree about who owns it.
            closed_index = newest_closed_index(rows_1h)
            if closed_index is None:
                logger.error("Every 1h candle is still forming; nothing to backfill")
                return
            if closed_index < len(rows_1h) - 1:
                logger.info(
                    "Excluding the forming 1h candle at %s (closes %s)",
                    rows_1h[closed_index + 1]["time"],
                    rows_1h[closed_index + 1].get("close_time"),
                )
                rows_1h = rows_1h[: closed_index + 1]

            logger.info(f"Found {len(rows_1h)} 1h OHLCV rows to process")

            if len(rows_1h) < INDICATOR_WINDOW_BARS:
                logger.error(
                    f"Not enough data. Have {len(rows_1h)}, need at least {INDICATOR_WINDOW_BARS}"
                )
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
                    if not dry_run:
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

            verb = "Would compute" if dry_run else "Computed"
            logger.info(f"Done! {verb} {processed} 1h rows ({errors} errors)")

    finally:
        # `pool` IS the global pool created by `init_pool`, so `close_pool`
        # closes it. Closing it twice would raise.
        await close_pool()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(compute_and_store_indicators(dry_run=args.dry_run))
