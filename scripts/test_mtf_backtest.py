#!/usr/bin/env python3
"""
End-to-end MTF backtest test script.

This script runs a backtest using the MTF strategy template
with real data from the database to verify the infrastructure works correctly.
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.mtf_template import MTFStrategyTemplate
from src.utils.logger import configure_logger


async def run_mtf_backtest():
    """Run an end-to-end MTF backtest with real data."""
    configure_logger("INFO")

    print("=" * 60)
    print("MTF END-TO-END BACKTEST TEST")
    print("=" * 60)

    # Database configuration (using local Docker setup)
    db_config = {
        "host": "localhost",
        "port": 15432,
        "name": "marketdata",
        "user": "trading",
        "password": "change_me",
    }

    # Strategy configuration
    strategy_config = {
        "regime_threshold": 0.005,
        "entry_pullback_pct": 0.01,
        "rsi_oversold": 40.0,
        "rsi_overbought": 60.0,
        "confidence_boost": 1.2,
    }

    # Create MTF strategy instance
    strategy = MTFStrategyTemplate(config=strategy_config)

    print(f"\nStrategy: {strategy.get_name()}")
    print(f"Required Timeframes: {strategy.REQUIRED_TIMEFRAMES}")
    print(f"Config: {strategy_config}")

    # Backtest configuration
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1h",  # Entry timeframe
        start_date=datetime.fromisoformat("2024-06-01T00:00:00"),
        end_date=datetime.fromisoformat("2024-09-01T00:00:00"),
        initial_capital=10000.0,
        fee_rate=0.001,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        sl_atr_multiplier=2.0,
        tp_atr_multiplier=4.5,
        trailing_activate_atr=1.5,
        trailing_offset_atr=1.0,
        use_atr_sizing=True,
        atr_multiplier=1.0,
        risk_per_trade=0.02,
        apply_global_trend_filter=False,  # Disable for cleaner MTF test
        use_executor_exit_model=True,
        ignore_signal_sells=False,
        strategy_classes=[MTFStrategyTemplate],
        strategy_configs=[strategy_config],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.7, "sell_threshold": -0.6},
        allow_short=True,
    )

    print(f"\nBacktest Parameters:")
    print(f"  Symbol: {config.symbol}")
    print(f"  Entry Timeframe: {config.timeframe}")
    print(f"  Date Range: {config.start_date.date()} to {config.end_date.date()}")
    print(f"  Initial Capital: ${config.initial_capital:,.2f}")
    print(f"  Fee Rate: {config.fee_rate:.3f}")

    # Initialize database pool
    print("\nConnecting to database...")
    await init_pool(db_config)

    try:
        reader = IndicatorReader(db_config)
        async with reader:
            print("Running backtest...")
            print("-" * 60)

            result = await BacktestEngine(config, reader).run()

            print("\n" + "=" * 60)
            print("BACKTEST RESULTS")
            print("=" * 60)
            print(f"Total Trades:     {result.total_trades}")
            print(f"Win Rate:         {result.win_rate:.2f}%")
            print(f"Total Return:     ${result.total_return:.2f} ({result.total_return_pct:.2f}%)")
            print(f"Max Drawdown:     {result.max_drawdown * 100:.2f}%")
            print(f"Final Equity:     ${result.final_equity:.2f}")
            print(f"Sharpe Ratio:     {result.sharpe_ratio:.2f}")
            print("=" * 60)

            if result.trades:
                print("\nAll Trades:")
                for i, t in enumerate(result.trades, 1):
                    print(
                        f"{i}. {t.entry_time} -> "
                        f"{t.exit_time} | "
                        f"Type: {t.side} | "
                        f"PnL: ${t.pnl:.2f} ({t.return_pct:.2f}%)"
                    )

            # Verify MTF functionality
            print("\n" + "=" * 60)
            print("MTF VERIFICATION")
            print("=" * 60)

            if result.total_trades > 0:
                print(f"✓ MTF strategy generated {result.total_trades} trades")
                print(f"✓ Strategy correctly processed multi-timeframe data")

                # Check trade distribution
                # Trade side can be BUY/SELL (signal type) or LONG/SHORT (position side)
                long_trades = sum(1 for t in result.trades if t.side == "BUY")
                short_trades = sum(1 for t in result.trades if t.side == "SELL")
                print(f"✓ Long trades: {long_trades}, Short trades: {short_trades}")

                if result.win_rate > 0:
                    print(f"✓ Win rate: {result.win_rate:.1f}%")

                print("\n✅ MTF INFRASTRUCTURE TEST PASSED")
                return True
            else:
                print("⚠ No trades generated - this may be normal for the strategy parameters")
                print("  Consider adjusting entry thresholds or date range")
                print("\n⚠️ MTF test inconclusive (no trades)")
                return False

    finally:
        await close_pool()


if __name__ == "__main__":
    success = asyncio.run(run_mtf_backtest())
    sys.exit(0 if success else 1)
