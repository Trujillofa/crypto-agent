#!/usr/bin/env python3
"""Compute and store indicators for any symbol/timeframe OHLCV history."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from src.db.pool import close_pool, get_pool, init_pool
from src.features.technical import TechnicalIndicators, compute_indicators
from src.features.writer import IndicatorWriter, StoredIndicator
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_LOOKBACK = 250


@dataclass(frozen=True)
class ComputeArgs:
    symbol: str
    timeframe: str
    lookback: int


def parse_args(argv: Sequence[str] | None = None) -> ComputeArgs:
    parser = argparse.ArgumentParser(
        description="Compute indicators for OHLCV history and store them in the indicators table."
    )
    parser.add_argument("--symbol", required=True, help="Trading pair, for example BTCUSDT")
    parser.add_argument("--timeframe", required=True, help="Candle timeframe, for example 4h")
    parser.add_argument(
        "--lookback",
        type=int,
        default=DEFAULT_LOOKBACK,
        help=f"Rolling window size used for indicator computation (default: {DEFAULT_LOOKBACK})",
    )
    parsed = parser.parse_args(argv)
    return ComputeArgs(
        symbol=parsed.symbol.upper(),
        timeframe=parsed.timeframe,
        lookback=parsed.lookback,
    )


def build_db_config(env: Mapping[str, str] | None = None) -> dict[str, object]:
    source = env or os.environ
    return {
        "host": source.get("DB_HOST", "localhost"),
        "port": int(source.get("DB_PORT", 15432)),
        "name": source.get("DB_NAME", "marketdata"),
        "user": source.get("DB_USER", "trading"),
        "password": source.get("DB_PASSWORD", "change_me"),
    }


def rows_to_ohlcv_series(rows: Sequence[Mapping[str, object]]) -> dict[str, list[float]]:
    return {
        "open": [float(row["open_price"]) for row in rows],
        "high": [float(row["high_price"]) for row in rows],
        "low": [float(row["low_price"]) for row in rows],
        "close": [float(row["close_price"]) for row in rows],
        "volume": [float(row["volume"]) for row in rows],
    }


def build_stored_indicator(
    *,
    row_time: datetime,
    symbol: str,
    timeframe: str,
    indicators: TechnicalIndicators,
) -> StoredIndicator:
    return StoredIndicator(
        time=row_time,
        symbol=symbol,
        timeframe=timeframe,
        rsi_14=indicators.rsi_14,
        rsi_7=indicators.rsi_7,
        macd=indicators.macd,
        macd_signal=indicators.macd_signal,
        macd_hist=indicators.macd_hist,
        bb_upper_dist=indicators.bb_upper_dist,
        bb_lower_dist=indicators.bb_lower_dist,
        atr_14=indicators.atr_14,
        atr_pct=indicators.atr_pct,
        ema_8=indicators.ema_8,
        ema_10=indicators.ema_10,
        ema_14=indicators.ema_14,
        ema_21=indicators.ema_21,
        ema_24=indicators.ema_24,
        ema_30=indicators.ema_30,
        ema_12=indicators.ema_12,
        ema_26=indicators.ema_26,
        ema_50=indicators.ema_50,
        ema_200=indicators.ema_200,
        sma_20=indicators.sma_20,
        sma_40=indicators.sma_40,
        sma_50=indicators.sma_50,
        sma_60=indicators.sma_60,
        sma_200=indicators.sma_200,
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


async def fetch_ohlcv_rows(symbol: str, timeframe: str) -> list[Mapping[str, object]]:
    pool = get_pool()
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
    return [dict(row) for row in rows]


async def process_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    symbol: str,
    timeframe: str,
    lookback: int,
    writer: IndicatorWriter,
) -> tuple[int, int]:
    if len(rows) < lookback:
        raise ValueError(f"Not enough data. Have {len(rows)}, need at least {lookback}")

    processed = 0
    errors = 0

    for index in range(lookback, len(rows)):
        window = rows[max(0, index - lookback) : index + 1]
        data = rows_to_ohlcv_series(window)

        try:
            indicators = compute_indicators(data)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            if errors <= 5:
                logger.warning("Failed at row %s for %s %s: %s", index, symbol, timeframe, exc)
            elif errors == 6:
                logger.warning("Suppressing further error messages...")
            continue

        stored = build_stored_indicator(
            row_time=rows[index]["time"],
            symbol=symbol,
            timeframe=timeframe,
            indicators=indicators,
        )
        await writer.write_indicators(stored)
        processed += 1

        if processed % 500 == 0:
            logger.info(
                "Processed %s rows for %s %s (errors: %s)",
                processed,
                symbol,
                timeframe,
                errors,
            )

    return processed, errors


async def compute_and_store_indicators(args: ComputeArgs) -> tuple[int, int]:
    db_config = build_db_config()
    await init_pool(db_config)

    try:
        rows = await fetch_ohlcv_rows(args.symbol, args.timeframe)
        logger.info(
            "Found %s OHLCV rows to process for %s %s",
            len(rows),
            args.symbol,
            args.timeframe,
        )

        async with IndicatorWriter(db_config) as writer:
            processed, errors = await process_rows(
                rows,
                symbol=args.symbol,
                timeframe=args.timeframe,
                lookback=args.lookback,
                writer=writer,
            )

        logger.info(
            "Done! Computed and stored %s rows for %s %s (%s errors)",
            processed,
            args.symbol,
            args.timeframe,
            errors,
        )
        return processed, errors
    finally:
        await close_pool()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(compute_and_store_indicators(args))


if __name__ == "__main__":
    main()
