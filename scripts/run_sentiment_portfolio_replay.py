#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

# Add project root when invoked as python scripts/run_sentiment_portfolio_replay.py.
sys.path.append(os.getcwd())

from src.backtest.portfolio import PortfolioReplayConfig, PortfolioReplayEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run sentiment portfolio replay")
    parser.add_argument("--config", default="config/settings.sentiment_macro.yaml")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--replay-sentiment-log", required=True)
    parser.add_argument("--replay-sentiment-max-age-hours", type=float, default=24.0)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = load_settings(Path(args.config))
    strategy_classes, strategy_configs, aggregator_config = _resolve_strategy_config(
        settings.strategy
    )[:3]
    with Path(args.config).open("r", encoding="utf-8") as file_handle:
        raw_config = yaml.safe_load(file_handle) or {}
    trading_execution = raw_config.get("trading_execution", {})
    exit_rules = trading_execution.get("exit_rules", {}) or {}
    futures = raw_config.get("futures", {})
    db_config = {
        "host": os.getenv("DB_HOST", settings.database.get("host", "localhost")),
        "port": int(os.getenv("DB_PORT", settings.database.get("port", 5432))),
        "name": os.getenv("DB_NAME", settings.database.get("name", "marketdata")),
        "user": os.getenv("DB_USER", settings.database.get("user", "trading")),
        "password": os.getenv("DB_PASSWORD", settings.database.get("password", "")),
    }
    config = PortfolioReplayConfig(
        symbols=settings.trading_pairs,
        timeframe=settings.timeframe,
        start_date=args.start,
        end_date=args.end,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
        replay_sentiment_path=args.replay_sentiment_log,
        replay_sentiment_max_age_seconds=args.replay_sentiment_max_age_hours * 3600,
        global_trend_filter_buffer_pct=settings.strategy.global_trend_filter_buffer_pct,
        max_concurrent_longs=int(futures.get("max_concurrent_longs", 1)),
        sl_cooldown_minutes=float(futures.get("sl_cooldown_minutes", 0)),
        order_size_usdt=settings.trading_execution.order_size_usdt,
        sl_atr_multiplier=float(trading_execution.get("sl_atr_multiplier", 2.0)),
        tp_atr_multiplier=float(trading_execution.get("tp_atr_multiplier", 3.5)),
        trailing_activate_atr=float(trading_execution.get("trailing_activate_atr", 1.5)),
        trailing_offset_atr=float(trading_execution.get("trailing_offset_atr", 1.0)),
        time_stop_minutes=float(exit_rules.get("time_stop_minutes", 0)),
    )
    await init_pool(db_config)
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            result = await PortfolioReplayEngine(config, reader).run()
    finally:
        await close_pool()

    print("SENTIMENT PORTFOLIO REPLAY")
    print(f"Symbols:              {', '.join(config.symbols)}")
    print(f"Trades:               {len(result.trades)}")
    print(f"Win rate:             {result.win_rate:.2f}%")
    print(f"Total P&L:            {result.total_pnl:.4f} USDT")
    print(f"Profit factor:        {result.profit_factor:.2f}")
    print(f"Skipped slot buys:    {result.skipped_slot_buys}")
    print(f"Skipped cooldown buys:{result.skipped_cooldown_buys:>5}")
    for trade in result.trades:
        print(
            f"{trade.entry_time} -> {trade.exit_time} {trade.symbol} "
            f"{trade.exit_reason} {trade.pnl:+.4f} USDT ({trade.return_pct:+.2f}%)"
        )


if __name__ == "__main__":
    asyncio.run(main())
