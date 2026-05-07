#!/usr/bin/env python3
"""Compute indicators for downloaded historical data.

Supports CLI arguments for symbol and timeframe, with batch inserts for performance.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import argparse
import asyncio
import os
import time

import asyncpg

from src.features.technical import compute_indicators
from src.utils.logger import get_logger

logger = get_logger(__name__)

UPSERT_SQL = """
    INSERT INTO indicators (
        time, symbol, timeframe,
        ema_8, ema_10, ema_12, ema_14, ema_21, ema_24,
        ema_26, ema_30, ema_50, ema_200,
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
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31, $32, $33, $34, $35, $36, $37)
    ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
        ema_8 = EXCLUDED.ema_8,
        ema_10 = EXCLUDED.ema_10,
        ema_12 = EXCLUDED.ema_12,
        ema_14 = EXCLUDED.ema_14,
        ema_21 = EXCLUDED.ema_21,
        ema_24 = EXCLUDED.ema_24,
        ema_26 = EXCLUDED.ema_26,
        ema_30 = EXCLUDED.ema_30,
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
"""


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Compute and store indicators for historical OHLCV data"
    )
    parser.add_argument(
        "--symbol", default="BTCUSDT", help="Trading pair symbol (default: BTCUSDT)"
    )
    parser.add_argument("--timeframe", default="4h", help="Candle timeframe (default: 4h)")
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Batch insert size (default: 500)"
    )
    parser.add_argument(
        "--lookback", type=int, default=750, help="Minimum lookback periods (default: 750)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Force full recompute even if indicators exist"
    )
    return parser.parse_args()


async def _flush_batch(conn: asyncpg.Connection, batch: list[tuple]) -> int:
    """Insert a batch of indicator rows using executemany for performance."""
    if not batch:
        return 0
    await conn.executemany(UPSERT_SQL, batch)
    return len(batch)


async def compute_and_store_indicators(
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    batch_size: int = 500,
    min_required: int = 750,
    force: bool = False,
) -> None:
    """Compute indicators for OHLCV data and store in indicators table.

    Args:
        symbol: Trading pair (e.g. BTCUSDT, ETHUSDT).
        timeframe: Candle timeframe (e.g. 4h, 1h, 15m).
        batch_size: Number of rows to batch before flushing to DB.
        min_required: Minimum lookback periods for indicator computation.
    """
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
            rows = await conn.fetch(
                """
                SELECT time, open_price, high_price, low_price, close_price, volume
                FROM ohlcv
                WHERE symbol = $1 AND timeframe = $2
                ORDER BY time ASC
                """,
                symbol,
                timeframe,
            )

            logger.info(f"Found {len(rows)} OHLCV rows for {symbol} {timeframe}")

            if len(rows) < min_required:
                logger.error(f"Not enough data. Have {len(rows)}, need at least {min_required}")
                return

            # Skip rows that already have indicators (resume support)
            start_idx = min_required
            if not force:
                last_indicator = await conn.fetchrow(
                    "SELECT max(time) as max_t FROM indicators WHERE symbol = $1 AND timeframe = $2",
                    symbol,
                    timeframe,
                )
                last_t = (
                    last_indicator["max_t"] if last_indicator and last_indicator["max_t"] else None
                )

                if last_t:
                    # Find the index of the first row AFTER the last indicator
                    for idx, row in enumerate(rows):
                        if row["time"] > last_t:
                            start_idx = max(start_idx, idx)
                            break
                    else:
                        logger.info("All rows already have indicators. Nothing to do.")
                        return
                    logger.info(f"Resuming from index {start_idx} (after {last_t})")
            else:
                logger.info("Force mode: recomputing all indicators from scratch")

            total_to_process = len(rows) - start_idx
            if total_to_process <= 0:
                logger.info("No new rows to process.")
                return

            processed = 0
            batch: list[tuple] = []
            t0 = time.monotonic()

            for i in range(start_idx, len(rows)):
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

                batch.append(
                    (
                        row_time,
                        symbol,
                        timeframe,
                        indicators.ema_8,
                        indicators.ema_10,
                        indicators.ema_12,
                        indicators.ema_14,
                        indicators.ema_21,
                        indicators.ema_24,
                        indicators.ema_26,
                        indicators.ema_30,
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
                )

                if len(batch) >= batch_size:
                    await _flush_batch(conn, batch)
                    processed += len(batch)
                    elapsed = time.monotonic() - t0
                    rate = processed / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"Processed {processed}/{total_to_process} rows "
                        f"({processed * 100 / total_to_process:.1f}%, {rate:.0f} rows/s)"
                    )
                    batch.clear()

            # Flush remaining
            if batch:
                await _flush_batch(conn, batch)
                processed += len(batch)

            elapsed = time.monotonic() - t0
            logger.info(
                f"Done! Computed indicators for {processed} rows of {symbol} {timeframe} "
                f"in {elapsed:.1f}s ({processed / elapsed:.0f} rows/s)"
            )

    finally:
        await pool.close()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        compute_and_store_indicators(
            symbol=args.symbol,
            timeframe=args.timeframe,
            batch_size=args.batch_size,
            min_required=args.lookback,
            force=args.force,
        )
    )
