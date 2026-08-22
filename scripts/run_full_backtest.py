#!/usr/bin/env python3
import argparse
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.backtest.research_safety import refuse_live_go
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.bollinger_strategy import BollingerBounceStrategy
from src.strategy.macd_strategy import MACDHistogramStrategy
from src.strategy.momentum_strategy import MomentumStrategy


async def main():
    refuse_live_go(argv=sys.argv[1:])
    parser = argparse.ArgumentParser(description="Run crypto strategy backtest")
    parser.add_argument("--symbol", type=str, required=True, help="Trading pair (e.g. BTCUSDT)")
    parser.add_argument("--timeframe", type=str, default="1m", help="Timeframe (e.g. 1m, 5m, 1h)")
    parser.add_argument("--start", type=str, required=True, help="Start date (ISO 8601)")
    parser.add_argument("--end", type=str, required=True, help="End date (ISO 8601)")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital")
    parser.add_argument("--fee", type=float, default=0.001, help="Trading fee rate (0.001 = 0.1%%)")

    args = parser.parse_args()

    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
        "name": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", "trading"),
    }

    strategies = [MACDHistogramStrategy, BollingerBounceStrategy, MomentumStrategy]

    strategy_configs = [
        {"min_histogram_threshold": 0.0, "use_atr_filter": True},
        {"band_distance_threshold": 0.0, "rsi_oversold": 30.0, "rsi_overbought": 70.0},
        {"rsi_buy_threshold": 60.0},
    ]

    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        fee_rate=args.fee,
        strategy_classes=strategies,
        strategy_configs=strategy_configs,
        aggregator_config={
            "min_agreement": 2,
            "buy_threshold": 0.5,
            "sell_threshold": -0.5,
        },
    )

    print(f"Starting backtest for {args.symbol} from {args.start} to {args.end}...")
    print(f"Strategies: {[s.__name__ for s in strategies]}")

    await init_pool(db_config)
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            result = await BacktestEngine(config, reader).run()
    finally:
        await close_pool()

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
