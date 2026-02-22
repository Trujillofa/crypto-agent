#!/usr/bin/env python3
import asyncio
import argparse
import os
import sys
from datetime import datetime

# Add project root to path
sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.utils.logger import configure_logger
from src.main import load_settings, _resolve_strategy_config
from pathlib import Path


async def main():
    configure_logger("INFO")
    parser = argparse.ArgumentParser(description="Run crypto strategy backtest")
    parser.add_argument(
        "--symbol", type=str, required=True, help="Trading pair (e.g. BTCUSDT)"
    )
    parser.add_argument(
        "--timeframe", type=str, default="1m", help="Timeframe (e.g. 1m, 5m, 1h)"
    )
    parser.add_argument(
        "--start", type=str, required=True, help="Start date (ISO 8601)"
    )
    parser.add_argument("--end", type=str, required=True, help="End date (ISO 8601)")
    parser.add_argument(
        "--capital", type=float, default=10000.0, help="Initial capital"
    )
    parser.add_argument(
        "--fee", type=float, default=0.001, help="Trading fee rate (0.001 = 0.1%%)"
    )
    parser.add_argument(
        "--sl", type=float, default=0.0, help="Stop loss percentage (e.g. 0.01 for 1%%)"
    )
    parser.add_argument(
        "--tp", type=float, default=0.0, help="Take profit percentage (e.g. 0.02 for 2%%)"
    )
    parser.add_argument(
        "--config", type=str, default="config/settings.yaml", help="Path to config file"
    )

    args = parser.parse_args()

    # Load settings from config file
    try:
        settings = load_settings(Path(args.config))
        strategy_classes, strategy_configs, aggregator_config = _resolve_strategy_config(
            settings.strategy
        )
        print(f"Loaded configuration from {args.config}")
    except Exception as e:
        print(f"Failed to load config from {args.config}: {e}")
        # Fallback to defaults or exit? Let's exit to enforce config usage.
        return

    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
        "name": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", "trading"),
    }

    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=datetime.fromisoformat(args.start),
        end_date=datetime.fromisoformat(args.end),
        initial_capital=args.capital,
        fee_rate=args.fee,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )

    print(f"Starting backtest for {args.symbol} from {args.start} to {args.end}...")
    print(f"Strategies: {[s.__name__ for s in strategy_classes]}")
    print(f"Aggregator Config: {aggregator_config}")

    reader = IndicatorReader(db_config)
    async with reader:
        engine = BacktestEngine(config, reader)
        result = await engine.run()

    print("\n" + "=" * 40)
    print("BACKTEST RESULTS")
    print("=" * 40)
    print(f"Total Trades: {result.total_trades}")
    print(f"Win Rate:     {result.win_rate:.2f}%")
    print(f"Total Return: ${result.total_return:.2f} ({result.total_return_pct:.2f}%)")
    print(f"Max Drawdown: {result.max_drawdown * 100:.2f}%")
    print(f"Final Equity: ${result.final_equity:.2f}")
    print("=" * 40)

    if result.trades:
        print("\nLast 5 Trades:")
        for t in result.trades[-5:]:
            print(
                f"{t.entry_time} -> {t.exit_time} | Type: {t.side} | PnL: ${t.pnl:.2f} ({t.return_pct:.2f}%)"
            )


if __name__ == "__main__":
    asyncio.run(main())
